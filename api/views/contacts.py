from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Count, Max, Avg, OuterRef, Subquery, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from ..models import Contact, Order, KomplainOrder, KomplainLog, CustomerActivity
from ..serializers import ContactSerializer, KomplainOrderSerializer, CustomerActivitySerializer
from ..permissions import IsOwnerOrManager

class ContactViewSet(viewsets.ModelViewSet):
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Query efisien — gunakan Subquery untuk dianotasikan ke queryset kontak
        orders_subquery = Order.objects.filter(
            nomor_wa=OuterRef('nomor_wa')
        ).exclude(status_global='batal').values('nomor_wa').annotate(
            total=Sum('sisa_tagihan')
        ).values('total')[:1]

        qs = Contact.objects.annotate(
            annotated_piutang=Coalesce(Subquery(orders_subquery), 0)
        )

        # ✅ Filter pencarian di level server database
        search = self.request.query_params.get('search')
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(nama__icontains=search) |
                Q(nomor_wa__icontains=search) |
                Q(keterangan__icontains=search)
            )

        # ✅ Filter tab berpiutang di level server database
        tab = self.request.query_params.get('tab')
        if tab == 'piutang':
            qs = qs.filter(annotated_piutang__gt=0)

        return qs.order_by('-total_spent', '-total_order')

    @action(detail=False, methods=['post'], url_path='sync')
    def sync(self, request):
        """
        POST /api/contacts/sync/
        Sinkronisasi data Contact dari semua Order yang ada.
        Dipanggil on-demand, menggunakan bulk_create/bulk_update agar sangat cepat.
        """
        # 1. Fetch mapping nama terbaru secara efisien
        name_map = {}
        for o in Order.objects.values('nomor_wa', 'nama', 'waktu').order_by('waktu'):
            name_map[o['nomor_wa']] = o['nama']

        # 2. Ambil agregat data statistik per nomor_wa
        stats = Order.objects.values('nomor_wa').annotate(
            order_count=Count('id', distinct=True),
            latest_order=Max('waktu'),
            spent_sum=Coalesce(Sum('items__harga_jual'), 0)
        )

        contacts_to_update = []
        contacts_to_create = []
        existing_contacts = {c.nomor_wa: c for c in Contact.objects.all()}

        for item in stats:
            wa = item['nomor_wa']
            name = name_map.get(wa, wa)
            order_count = item['order_count']
            last_date = item['latest_order'].date() if item['latest_order'] else None
            spent = item['spent_sum']

            if wa in existing_contacts:
                contact = existing_contacts[wa]
                contact.nama = name
                contact.total_order = order_count
                contact.last_order = last_date
                contact.total_spent = spent
                contacts_to_update.append(contact)
            else:
                contacts_to_create.append(Contact(
                    nomor_wa=wa,
                    nama=name,
                    total_order=order_count,
                    last_order=last_date,
                    total_spent=spent
                ))

        if contacts_to_create:
            Contact.objects.bulk_create(contacts_to_create)
        if contacts_to_update:
            Contact.objects.bulk_update(contacts_to_update, ['nama', 'total_order', 'last_order', 'total_spent'])

        return Response({'ok': True, 'synced': len(stats)})

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/contacts/stats/
        Mendapatkan data statistik ringkasan pelanggan secara langsung dari database (sangat cepat).
        """
        # 1. Hitung total piutang dari pesanan yang tidak batal
        total_piutang = Order.objects.exclude(status_global='batal').aggregate(
            total=Sum('sisa_tagihan')
        )['total'] or 0
        
        # 2. Hitung total revenue dari contact total_spent
        total_spent_agg = Contact.objects.aggregate(
            total_revenue=Sum('total_spent'),
            total_customers=Count('nomor_wa'),
            total_orders=Sum('total_order')
        )
        
        total_revenue = total_spent_agg['total_revenue'] or 0
        total_customers = total_spent_agg['total_customers'] or 0
        total_orders = total_spent_agg['total_orders'] or 0
        
        # 3. Hitung rata-rata nilai order
        avg_order_value = 0
        if total_orders > 0:
            avg_order_value = total_revenue / total_orders
            
        # 4. Top 5 Customers
        top_5 = Contact.objects.order_by('-total_spent')[:5]
        top_5_data = ContactSerializer(top_5, many=True).data
        
        return Response({
            'total_customers': total_customers,
            'total_revenue': total_revenue,
            'total_piutang': total_piutang,
            'avg_order_value': avg_order_value,
            'top_customers': top_5_data
        })


class ContactStatsView(APIView):
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        total_customers = Contact.objects.count()
        
        total_revenue = Order.objects.aggregate(
            total=Sum('total_harga')
        )['total'] or 0
        
        total_piutang = Order.objects.filter(
            status_global__in=['review', 'desain', 'proses']
        ).aggregate(total=Sum('sisa_tagihan'))['total'] or 0
        
        top_customers = Contact.objects.order_by('-total_spent')[:5]
        
        avg_order_value = (
            total_revenue / max(1, total_customers)
            if total_customers > 0 else 0
        )
        
        return Response({
            'total_customers': total_customers,
            'total_revenue': total_revenue,
            'avg_order_value': int(avg_order_value),
            'total_piutang': total_piutang,
            'top_customers': ContactSerializer(top_customers, many=True).data,
        })


class KomplainViewSet(viewsets.ModelViewSet):
    serializer_class = KomplainOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = KomplainOrder.objects.select_related(
            'order', 'dicatat_oleh', 'ditangani_oleh'
        ).prefetch_related('logs')
        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        # Filter by order_id if provided
        order_id = self.request.query_params.get('order')
        if order_id:
            qs = qs.filter(order_id=order_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(dicatat_oleh=self.request.user)

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        """Manager menyelesaikan komplain dengan menetapkan resolusi."""
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Hanya Owner/Manager yang dapat menyelesaikan komplain.'}, status=403)

        komplain = get_object_or_404(KomplainOrder, pk=pk)
        resolusi = request.data.get('resolusi')
        catatan  = request.data.get('catatan_resolusi', '')
        status_baru = request.data.get('status', 'selesai')

        if not resolusi:
            return Response({'error': 'Field resolusi wajib diisi.'}, status=400)

        with transaction.atomic():
            komplain.resolusi         = resolusi
            komplain.catatan_resolusi = catatan
            komplain.status           = status_baru
            komplain.ditangani_oleh   = request.user
            if status_baru == 'selesai':
                komplain.waktu_selesai = timezone.now()
            komplain.save()

            KomplainLog.objects.create(
                komplain=komplain,
                user=request.user,
                status_baru=status_baru,
                catatan=f"Resolusi: {resolusi}. {catatan}".strip(),
            )

        return Response(KomplainOrderSerializer(komplain).data)

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """Update status komplain dan tambahkan log entry."""
        komplain = get_object_or_404(KomplainOrder, pk=pk)
        status_baru = request.data.get('status')
        catatan     = request.data.get('catatan', '')

        if not status_baru:
            return Response({'error': 'Field status wajib diisi.'}, status=400)

        valid_statuses = [s[0] for s in KomplainOrder.STATUS_CHOICES]
        if status_baru not in valid_statuses:
            return Response({'error': f'Status tidak valid. Pilihan: {valid_statuses}'}, status=400)

        with transaction.atomic():
            komplain.status = status_baru
            if status_baru == 'selesai':
                komplain.waktu_selesai = timezone.now()
            komplain.save()

            KomplainLog.objects.create(
                komplain=komplain,
                user=request.user,
                status_baru=status_baru,
                catatan=catatan,
            )

        return Response(KomplainOrderSerializer(komplain).data)


class CustomerActivityViewSet(viewsets.ModelViewSet):
    queryset = CustomerActivity.objects.select_related('order', 'pic').order_by('waktu_jatuh_tempo')
    serializer_class = CustomerActivitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        order_id = self.request.query_params.get('order')
        if order_id:
            qs = qs.filter(order_id=order_id)
        
        selesai_filter = self.request.query_params.get('selesai')
        if selesai_filter == 'true':
            qs = qs.filter(selesai=True)
        elif selesai_filter == 'false':
            qs = qs.filter(selesai=False)

        # Staff hanya melihat task miliknya
        if self.request.user.role == 'staff':
            qs = qs.filter(pic=self.request.user)

        return qs

    def perform_create(self, serializer):
        serializer.save(pic=self.request.user)

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        activity = self.get_object()
        activity.selesai = True
        activity.waktu_selesai = timezone.now()
        activity.save()
        return Response(CustomerActivitySerializer(activity).data)

