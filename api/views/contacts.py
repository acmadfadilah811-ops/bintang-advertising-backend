from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Count, Max, Avg, OuterRef, Subquery
from django.db.models.functions import Coalesce

from ..models import Contact, Order
from ..serializers import ContactSerializer
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
