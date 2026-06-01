from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, BasePermission, AllowAny
from rest_framework.decorators import action
from django.db.models import Count, Sum, Q, F
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404
import uuid
import re as _re
from .models import Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard, InventoryItem, RestockHistory, ProductPrice, SystemConfig, FAQ
from .serializers import (
    DivisiSerializer, TahapProsesSerializer, CustomUserSerializer,
    ContactSerializer, OrderSerializer, OrderItemSerializer, JobBoardSerializer,
    InventoryItemSerializer, ProductPriceSerializer, SystemConfigSerializer, FAQSerializer,
    BusinessSettingsSerializer,
)

# ---------------------------------------------------------
# CUSTOM PERMISSION — Hanya Owner atau Manager
# ---------------------------------------------------------
class IsOwnerOrManager(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']
        )

# ---------------------------------------------------------
# CUSTOM PERMISSION — Harus Clock In (Untuk Staff)
# ---------------------------------------------------------
class IsClockedIn(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        # Owner/Manager/Admin bebas akses tanpa clock-in
        if getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']:
            return True
        
        # Cek absensi staff hari ini: jam_masuk ada, jam_keluar kosong ATAU workspace_unlocked di-enable oleh Owner
        from hr.models import Absensi
        from django.utils import timezone
        today = timezone.localdate()
        
        return Absensi.objects.filter(
            Q(staff=request.user, tanggal=today, jam_masuk__isnull=False, jam_keluar__isnull=True) |
            Q(staff=request.user, tanggal=today, jam_masuk__isnull=False, workspace_unlocked=True)
        ).exists()

class DivisiViewSet(viewsets.ModelViewSet):
    queryset = Divisi.objects.all()
    serializer_class = DivisiSerializer
    permission_classes = [IsAuthenticated]

class TahapProsesViewSet(viewsets.ModelViewSet):
    queryset = TahapProses.objects.all()
    serializer_class = TahapProsesSerializer
    permission_classes = [IsAuthenticated]

class CustomUserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = CustomUser.objects.all()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    @action(detail=False, methods=['get', 'patch'], url_path='me')
    def me(self, request):
        user = request.user
        if request.method == 'GET':
            serializer = self.get_serializer(user)
            return Response(serializer.data)
        elif request.method == 'PATCH':
            serializer = self.get_serializer(user, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=400)

    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        # Hanya Owner atau Manager yang boleh mereset password
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Hanya Owner atau Manager yang dapat mereset password.'}, status=status.HTTP_403_FORBIDDEN)
        
        user_to_reset = self.get_object()
        
        # Manager tidak boleh mereset password Owner atau Manager lain
        if request.user.role == 'manager' and user_to_reset.role in ['owner', 'manager']:
            return Response({'error': 'Manager tidak boleh mereset password Owner atau Manager.'}, status=status.HTTP_403_FORBIDDEN)
            
        new_password = request.data.get('new_password')
        if not new_password:
            return Response({'error': 'Password baru wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(new_password, user=user_to_reset)
        except DjangoValidationError as e:
            return Response({'error': ", ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)
            
        user_to_reset.set_password(new_password)
        user_to_reset.save()
        
        # Catat audit log
        from users.models import SecurityAuditLog
        SecurityAuditLog.objects.create(
            user=request.user,
            username_input=request.user.username,
            event="PASSWORD_CHANGED",
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan=f"Reset password untuk user {user_to_reset.username} oleh {request.user.role}",
            berhasil=True,
        )
        
        return Response({'message': f'Password untuk {user_to_reset.username} berhasil diubah.'}, status=status.HTTP_200_OK)


class ContactViewSet(viewsets.ModelViewSet):
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Query efisien — gunakan Subquery untuk dianotasikan ke queryset kontak
        from django.db.models import OuterRef, Subquery, Sum
        from django.db.models.functions import Coalesce

        orders_subquery = Order.objects.filter(
            nomor_wa=OuterRef('nomor_wa')
        ).exclude(status_global='batal').values('nomor_wa').annotate(
            total=Sum('sisa_tagihan')
        ).values('total')

        return Contact.objects.annotate(
            annotated_piutang=Coalesce(Subquery(orders_subquery), 0)
        ).order_by('-total_order', '-total_spent')

    @action(detail=False, methods=['post'], url_path='sync')
    def sync(self, request):
        """
        POST /api/contacts/sync/
        Sinkronisasi data Contact dari semua Order yang ada.
        Dipanggil on-demand, menggunakan bulk_create/bulk_update agar sangat cepat.
        """
        from django.db.models import Max, Sum, Count
        from django.db.models.functions import Coalesce

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

# ---------------------------------------------------------
# INVENTORY VIEWSET
# ---------------------------------------------------------
class InventoryItemViewSet(viewsets.ModelViewSet):
    serializer_class   = InventoryItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = InventoryItem.objects.prefetch_related('history').order_by('kategori', 'nama')
        if kat := self.request.query_params.get('kategori'):
            qs = qs.filter(kategori__icontains=kat)
        if q := self.request.query_params.get('search'):
            qs = qs.filter(nama__icontains=q)
        if self.request.query_params.get('kritis') == 'true':
            from django.db.models import F
            qs = qs.filter(stok__lt=F('min_stok'))
        return qs

    def perform_create(self, serializer):
        """Auto-generate ID: INV-YYYYMMDD-XXXX"""
        from django.utils import timezone
        today    = timezone.now().strftime('%Y%m%d')
        short_id = uuid.uuid4().hex[:4].upper()
        inv_id   = f'INV-{today}-{short_id}'
        serializer.save(id=inv_id)

    @action(detail=True, methods=['post'], url_path='restock')
    def restock(self, request, pk=None):
        """
        POST /api/inventory/{id}/restock/
        Body: { delta: float, keterangan: str }
        Mencatat riwayat dan update stok.
        """
        item       = self.get_object()
        delta_raw  = request.data.get('delta')
        keterangan = request.data.get('keterangan', '')

        if delta_raw is None:
            return Response(
                {'error': 'delta wajib diisi (positif = tambah, negatif = kurangi).'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            delta = float(delta_raw)
        except (ValueError, TypeError):
            return Response({'error': 'delta harus berupa angka.'}, status=status.HTTP_400_BAD_REQUEST)

        stok_awal  = item.stok
        stok_akhir = max(0.0, item.stok + delta)

        # Simpan history
        RestockHistory.objects.create(
            item       = item,
            user       = request.user,
            delta      = delta,
            stok_awal  = stok_awal,
            stok_akhir = stok_akhir,
            keterangan = keterangan,
        )

        # Update stok
        item.stok = stok_akhir
        item.save()

        return Response(InventoryItemSerializer(item).data)


# ---------------------------------------------------------
# RESTOCK VIEW — Standalone APIView (lebih reliable dari @action)
# ---------------------------------------------------------
class InventoryRestockView(APIView):
    """POST /api/inventory/<pk>/restock/ — Tambah/kurangi stok dan catat history."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        item       = get_object_or_404(InventoryItem, pk=pk)
        delta_raw  = request.data.get('delta')
        keterangan = request.data.get('keterangan', '')

        if delta_raw is None:
            return Response(
                {'error': 'delta wajib diisi (+ tambah, - kurangi)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            delta = float(delta_raw)
        except (ValueError, TypeError):
            return Response(
                {'error': 'delta harus berupa angka'},
                status=status.HTTP_400_BAD_REQUEST
            )

        stok_awal  = item.stok
        stok_akhir = max(0.0, item.stok + delta)

        RestockHistory.objects.create(
            item       = item,
            user       = request.user,
            delta      = delta,
            stok_awal  = stok_awal,
            stok_akhir = stok_akhir,
            keterangan = keterangan,
        )

        item.stok = stok_akhir
        item.save()

        return Response({
            'ok':       True,
            'id':       item.id,
            'nama':     item.nama,
            'stok_baru': stok_akhir,
        }, status=status.HTTP_200_OK)


def deduct_job_materials_if_needed(job, user):
    """
    Mengecek catatan_staff untuk job ini. Jika ada pemakaian inventori (item_id & jumlah/qty > 0)
    dan belum pernah dipotong (belum ada RestockHistory untuk Job #{job.id}),
    maka potong stoknya secara otomatis.
    """
    from django.db import transaction
    from .models import InventoryItem, RestockHistory
    
    marker = f"Job #{job.id}"
    if RestockHistory.objects.filter(keterangan__icontains=marker).exists():
        return
        
    materials_list = job.catatan_staff if isinstance(job.catatan_staff, list) else []
    if not materials_list:
        return
        
    # Cari indeks pembatas terakhir
    last_sep_idx = -1
    for i, mat in enumerate(materials_list):
        if str(mat.get('keterangan', '')).startswith('--- Dari Divisi:'):
            last_sep_idx = i
            
    current_mats = materials_list[last_sep_idx+1:] if last_sep_idx != -1 else materials_list
    
    with transaction.atomic():
        for mat in current_mats:
            item_id = mat.get('item_id', '')
            if not item_id:
                continue
                
            qty_val = mat.get('jumlah') or mat.get('qty') or 0
            try:
                qty = float(str(qty_val).replace(',', '.'))
            except (ValueError, TypeError):
                continue
                
            if qty <= 0:
                continue
                
            try:
                item = InventoryItem.objects.select_for_update().get(pk=item_id)
            except InventoryItem.DoesNotExist:
                continue
                
            stok_awal = item.stok
            stok_akhir = max(0.0, round(item.stok - qty, 4))
            catatan_mat = mat.get('catatan', '')
            
            RestockHistory.objects.create(
                item=item,
                user=user,
                delta=-qty,
                stok_awal=stok_awal,
                stok_akhir=stok_akhir,
                keterangan=f"Pemakaian produksi otomatis | Job #{job.id} | {catatan_mat}".strip(' |'),
            )
            
            item.stok = stok_akhir
            item.save()


# ---------------------------------------------------------
# JOB MATERIAL DEDUCT VIEW — Kurangi stok saat finishing
# ---------------------------------------------------------
class JobMaterialDeductView(APIView):
    """
    POST /api/jobs/{job_id}/use-materials/
    Body: { materials: [{item_id: "INV-xxx", qty: 1.2, catatan: "..."}, ...] }
    Mengurangi stok inventori & mencatat RestockHistory per item.
    """
    permission_classes = [IsAuthenticated, IsClockedIn]

    def post(self, request, job_id):
        job = get_object_or_404(JobBoard, pk=job_id)

        # Staff hanya bisa input untuk job miliknya
        if request.user.role == 'staff' and job.pic_staff != request.user:
            return Response({'error': 'Akses ditolak.'}, status=status.HTTP_403_FORBIDDEN)

        materials = request.data.get('materials', [])
        if not materials:
            return Response({'error': 'Tidak ada bahan yang diinput.'}, status=status.HTTP_400_BAD_REQUEST)

        deducted = []
        errors   = []

        with transaction.atomic():
            for mat in materials:
                item_id = mat.get('item_id', '').strip()
                catatan = mat.get('catatan', '')

                # Parse qty — toleran terhadap koma desimal (1,2 → 1.2)
                try:
                    qty = float(str(mat.get('qty', 0)).replace(',', '.'))
                except (ValueError, TypeError):
                    errors.append(f"Qty tidak valid untuk item {item_id}")
                    continue

                if qty <= 0:
                    continue

                try:
                    item = InventoryItem.objects.select_for_update().get(pk=item_id)
                except InventoryItem.DoesNotExist:
                    errors.append(f"Item '{item_id}' tidak ditemukan di inventori.")
                    continue

                stok_awal  = item.stok
                stok_akhir = max(0.0, round(item.stok - qty, 4))

                RestockHistory.objects.create(
                    item       = item,
                    user       = request.user,
                    delta      = -qty,
                    stok_awal  = stok_awal,
                    stok_akhir = stok_akhir,
                    keterangan = f"Pemakaian produksi | Job #{job_id} | {catatan}".strip(' |'),
                )

                item.stok = stok_akhir
                item.save()

                deducted.append({
                    'item_id':  item.id,
                    'nama':     item.nama,
                    'satuan':   item.satuan,
                    'qty_used': qty,
                    'stok_baru': stok_akhir,
                })

        return Response({
            'ok':       True,
            'deducted': deducted,
            'errors':   errors,
            'total_items': len(deducted),
        }, status=status.HTTP_200_OK)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        base_qs = Order.objects.prefetch_related(
            'items__jobs',
            'items__jobs__tahap',
            'items__jobs__tahap__divisi',
            'items__jobs__pic_staff',
            'items__jobs__pic_staff__divisi'
        ).order_by('-waktu')
        if user.role in ['owner', 'manager', 'admin']:
            return base_qs
        my_order_ids = JobBoard.objects.filter(
            pic_staff=user
        ).values_list('order_item__order_id', flat=True)
        return base_qs.filter(id__in=my_order_ids)

    def perform_create(self, serializer):
        # Auto-generate ID: ORD-20260517-A3F2
        today = timezone.now().strftime('%Y%m%d')
        short_id = uuid.uuid4().hex[:4].upper()
        order_id = f'ORD-{today}-{short_id}'
        serializer.save(id=order_id)

    @action(detail=True, methods=['post'], url_path='bayar')
    def bayar(self, request, pk=None):
        order = self.get_object()
        jumlah_bayar = request.data.get('jumlah_bayar')
        metode = request.data.get('metode_pembayaran', 'tunai')

        if jumlah_bayar is None:
            return Response({'error': 'jumlah_bayar wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            jumlah_bayar = int(jumlah_bayar)
        except (ValueError, TypeError):
            return Response({'error': 'jumlah_bayar harus berupa angka bulat.'}, status=status.HTTP_400_BAD_REQUEST)

        order.dp_dibayar += jumlah_bayar
        order.metode_pembayaran = metode
        order.save()

        # Update statistik Contact
        try:
            contact = Contact.objects.get(nomor_wa=order.nomor_wa)
            my_orders = Order.objects.filter(nomor_wa=contact.nomor_wa).prefetch_related('items')
            contact.total_spent = sum(
                item.harga_jual
                for o in my_orders
                for item in o.items.all()
            )
            contact.save()
        except Contact.DoesNotExist:
            pass

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)


class AssignOrderView(APIView):
    """POST /api/orders/{order_id}/assign/ — Manager/Admin publish/assign staff ke semua item dalam order"""
    permission_classes = [IsOwnerOrManager]

    def post(self, request, order_id):
        staff_id = request.data.get('staff_id')
        tahap_id = request.data.get('tahap_id', None)
        divisi_id = request.data.get('divisi_id', None)
        status_global = request.data.get('status_global', None)
        biaya_desain = int(request.data.get('biaya_desain', 0))
        insentif = int(request.data.get('insentif', 0))

        order_item_id = request.data.get('order_item_id', None)

        # Validasi order ada
        try:
            order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'Order tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        staff = None
        tahap = None

        if staff_id:
            # Validasi staff ada
            try:
                staff = CustomUser.objects.get(pk=staff_id, role='staff')
            except CustomUser.DoesNotExist:
                return Response({'error': 'Staff tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        # Ambil tahap jika ada
        if tahap_id:
            try:
                from .models import TahapProses as TahapModel
                tahap = TahapModel.objects.get(pk=tahap_id)
            except Exception:
                pass

        # JIKA tahap masih None tapi ada divisi_id, ambil tahap pertama di divisi tersebut
        if not tahap and divisi_id:
            try:
                from .models import TahapProses as TahapModel
                tahap = TahapModel.objects.filter(divisi_id=divisi_id).order_by('urutan').first()
            except Exception:
                pass

        # JIKA tahap masih None tapi staff diisi, ambil tahap pertama divisi staff sebagai fallback
        if not tahap and staff and staff.divisi:
            try:
                from .models import TahapProses as TahapModel
                tahap = TahapModel.objects.filter(divisi=staff.divisi).order_by('urutan').first()
            except Exception:
                pass

        # Guard: setidaknya harus ada tahap/divisi jika tidak ada staff
        if not tahap and not staff:
            return Response({'error': 'staff_id atau divisi/tahap wajib diisi untuk penerbitan SPK.'}, status=status.HTTP_400_BAD_REQUEST)

        # Buat atau update JobBoard untuk setiap OrderItem
        items = order.items.all()
        if order_item_id:
            items = items.filter(pk=order_item_id)
            
        if not items.exists():
            return Response({'error': 'Order ini belum memiliki item produk atau item tidak cocok.'}, status=status.HTTP_400_BAD_REQUEST)

        # Buat atau update JobBoard — lookup by (order_item, tahap) bukan hanya order_item
        created_jobs = []
        for item in items:
            job, created = JobBoard.objects.update_or_create(
                order_item=item,
                tahap=tahap,          # <-- tambahkan tahap ke lookup key
                defaults={
                    'pic_staff': staff,
                    'status_pekerjaan': 'antrean',
                    'biaya_desain': biaya_desain,
                    'insentif': insentif,
                    'waktu_mulai': None,
                    'waktu_selesai': None,
                }
            )
            created_jobs.append({'job_id': job.id, 'item': item.jenis_produk, 'created': created})

        # Update status order
        if status_global:
            order.status_global = status_global
        else:
            # Fallback cerdas berdasarkan divisi staff/tahap
            is_desain = False
            if tahap and tahap.divisi and tahap.divisi.nama.lower() == 'desain':
                is_desain = True
            elif staff and staff.divisi and staff.divisi.nama.lower() == 'desain':
                is_desain = True
                
            if is_desain:
                order.status_global = 'desain'
            else:
                order.status_global = 'proses'
        order.save()

        target_name = staff.username if staff else (tahap.divisi.nama if tahap and tahap.divisi else 'Divisi')
        return Response({
            'message': f'Order {order_id} berhasil di-publish/assign ke {target_name}.',
            'jobs': created_jobs,
        }, status=status.HTTP_200_OK)

class OrderItemViewSet(viewsets.ModelViewSet):
    queryset = OrderItem.objects.all()
    serializer_class = OrderItemSerializer
    permission_classes = [IsAuthenticated]

class JobBoardViewSet(viewsets.ModelViewSet):
    serializer_class = JobBoardSerializer
    permission_classes = [IsAuthenticated, IsClockedIn]

    def get_queryset(self):
        user = self.request.user
        base_qs = JobBoard.objects.select_related(
            'tahap',
            'tahap__divisi',
            'pic_staff',
            'pic_staff__divisi',
            'order_item',
            'order_item__order'
        ).order_by('-id')
        
        # Owner, Manager & Admin bisa lihat semua job
        if user.role in ['owner', 'manager', 'admin']:
            return base_qs
            
        # Staff: bisa lihat job miliknya ATAU job unassigned di divisinya
        if user.divisi:
            return base_qs.filter(
                Q(pic_staff=user) | 
                Q(pic_staff__isnull=True, tahap__divisi=user.divisi)
            )
        return base_qs.filter(pic_staff=user)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsClockedIn])
    def claim(self, request, pk=None):
        """POST /api/jobs/{id}/claim/ — Staff mengklaim job unassigned milik divisinya"""
        job = self.get_object()
        user = request.user
        
        if job.pic_staff:
            return Response({'error': f'Job sudah diambil oleh {job.pic_staff.username}.'}, status=status.HTTP_400_BAD_REQUEST)
            
        if not user.divisi or (job.tahap and job.tahap.divisi != user.divisi):
            return Response({'error': 'Anda hanya dapat mengklaim pekerjaan dari divisi Anda sendiri.'}, status=status.HTTP_403_FORBIDDEN)
            
        job.pic_staff = user
        job.status_pekerjaan = 'antrean'
        job.save()
        
        return Response({
            'message': 'Pekerjaan berhasil diklaim.',
            'job': JobBoardSerializer(job).data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsClockedIn])
    def start(self, request, pk=None):
        """POST /api/jobs/{id}/start/ — Staff memulai pengerjaan job"""
        job = self.get_object()
        user = request.user
        
        if job.pic_staff != user:
            return Response({'error': 'Hanya PIC staff yang dapat memulai pekerjaan ini.'}, status=status.HTTP_403_FORBIDDEN)
            
        job.status_pekerjaan = 'dikerjakan'
        job.waktu_mulai = timezone.now()
        job.save()
        
        return Response({
            'message': 'Pekerjaan dimulai.',
            'job': JobBoardSerializer(job).data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsClockedIn])
    def complete(self, request, pk=None):
        """POST /api/jobs/{id}/complete/ — Staff menyelesaikan pengerjaan job secara langsung (bebas OTP)"""
        job = self.get_object()
        user = request.user
        
        if job.pic_staff != user:
            return Response({'error': 'Hanya PIC staff yang dapat menyelesaikan pekerjaan ini.'}, status=status.HTTP_403_FORBIDDEN)
            
        job.status_pekerjaan = 'selesai'
        job.waktu_selesai = timezone.now()
        # Reset OTP fields
        job.otp_code = ''
        job.otp_requested = False
        job.otp_sent = False
        job.save()
        
        # Potong bahan otomatis ke inventori
        deduct_job_materials_if_needed(job, user)
        
        # Cek apakah seluruh job dari semua item dalam pesanan ini sudah selesai
        order = job.order_item.order
        active_jobs_exist = JobBoard.objects.filter(
            order_item__order=order,
            status_pekerjaan__in=['antrean', 'dikerjakan', 'kendala']
        ).exists()

        if not active_jobs_exist:
            order.status_global = 'ready'
            order.save()
            
        return Response({
            'message': 'Pekerjaan berhasil diselesaikan.',
            'job': JobBoardSerializer(job).data
        }, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

# CATATAN: InventoryItemViewSet sudah didefinisikan lengkap di atas (baris ~126).
# Duplikat class yang lebih simpel ini dihapus agar get_queryset filter & restock @action aktif.

class ProductPriceViewSet(viewsets.ModelViewSet):
    queryset = ProductPrice.objects.all()
    serializer_class = ProductPriceSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='seed')
    def seed_prices(self, request):
        import os
        import json
        from django.conf import settings
        
        path = os.path.join(settings.BASE_DIR, '..', 'bintang_advertising_app', 'data', 'db_harga.json')
        if not os.path.exists(path):
            path = os.path.join(settings.BASE_DIR, 'db_harga.json')
            
        if not os.path.exists(path):
            return Response({"detail": "File db_harga.json tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)
            
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Hapus data lama
        ProductPrice.objects.all().delete()
        
        created_count = 0
        for cat_key, cat_val in data.items():
            for prod_name, prod_val in cat_val.items():
                if isinstance(prod_val, str):
                    clean_price = int(float(prod_val.replace('.', '')))
                    ProductPrice.objects.create(
                        kategori=cat_key,
                        nama_produk=prod_name,
                        harga=clean_price,
                        price_type='flat'
                    )
                    created_count += 1
                elif isinstance(prod_val, dict):
                    keys = list(prod_val.keys())
                    is_qty_tier = any('lbr' in k.lower() or 'pcs' in k.lower() or 'box' in k.lower() or '>' in k.lower() for k in keys)
                    
                    if is_qty_tier:
                        cleaned_tiers = {}
                        for tk, tv in prod_val.items():
                            cleaned_tiers[tk] = int(float(tv.replace('.', '')))
                        ProductPrice.objects.create(
                            kategori=cat_key,
                            nama_produk=prod_name,
                            price_type='tiered',
                            tiers=cleaned_tiers
                        )
                        created_count += 1
                    else:
                        for mat_name, mat_val in prod_val.items():
                            if isinstance(mat_val, str):
                                clean_price = int(float(mat_val.replace('.', '')))
                                ProductPrice.objects.create(
                                    kategori=cat_key,
                                    nama_produk=prod_name,
                                    material=mat_name,
                                    harga=clean_price,
                                    price_type='flat'
                                )
                                created_count += 1
                            elif isinstance(mat_val, dict):
                                cleaned_tiers = {}
                                for tk, tv in mat_val.items():
                                    cleaned_tiers[tk] = int(float(tv.replace('.', '')))
                                ProductPrice.objects.create(
                                    kategori=cat_key,
                                    nama_produk=prod_name,
                                    material=mat_name,
                                    price_type='tiered',
                                    tiers=cleaned_tiers
                                )
                                created_count += 1
                                
        return Response({"detail": f"Berhasil mengimpor {created_count} produk dari db_harga.json."})

class SystemConfigViewSet(viewsets.ModelViewSet):
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    permission_classes = [IsAuthenticated]

class FAQViewSet(viewsets.ModelViewSet):
    queryset = FAQ.objects.all()
    serializer_class = FAQSerializer
    permission_classes = [IsAuthenticated]


# ---------------------------------------------------------
# BUSINESS SETTINGS VIEW — GET/PATCH pengaturan bisnis
# Mirip OrgSettingsView di Django CRM, Divisi sebagai "org"
# ---------------------------------------------------------
class BusinessSettingsView(APIView):
    """
    GET  /api/business-settings/ — Baca pengaturan bisnis
    PATCH /api/business-settings/ — Update pengaturan bisnis (Owner/Manager only)

    Pengaturan disimpan di SystemConfig dengan prefix 'bisnis_'.
    Divisi digunakan sebagai satuan organisasi/departemen.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Kembalikan semua pengaturan bisnis + daftar divisi."""
        serializer = BusinessSettingsSerializer(instance={}, context={'request': request})
        return Response(serializer.data)

    def patch(self, request):
        """Update pengaturan bisnis — hanya Owner atau Manager."""
        if getattr(request.user, 'role', '') not in ['owner', 'manager']:
            return Response(
                {'error': 'Hanya Owner atau Manager yang dapat mengubah pengaturan bisnis.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = BusinessSettingsSerializer(data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            serializer.save()
            # Kembalikan data terbaru setelah disimpan
            return Response(BusinessSettingsSerializer(instance={}, context={'request': request}).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------
# DASHBOARD VIEW — Agregasi data untuk halaman Dashboard
# ---------------------------------------------------------
class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import calendar
        from datetime import timedelta

        # Ambil waktu sekarang sesuai timezone lokal
        now = timezone.localtime(timezone.now())
        
        # 1. Rentang waktu hari ini
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        
        # 2. Rentang waktu bulan ini
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Cari hari terakhir di bulan ini
        _, last_day = calendar.monthrange(now.year, now.month)
        month_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)

        # --- Total Order ---
        # Ganti __date dan __month dengan rentang (__gte, __lt) agar aman di SQLite
        total_order_hari_ini = Order.objects.filter(waktu__gte=today_start, waktu__lt=today_end).count()
        total_order_bulan_ini = Order.objects.filter(waktu__gte=month_start, waktu__lte=month_end).count()

        # --- Omset Bulan Ini ---
        omset_bulan_ini = OrderItem.objects.filter(
            order__waktu__gte=month_start,
            order__waktu__lte=month_end
        ).aggregate(total=Sum('harga_jual'))['total'] or 0

        # --- Omset 6 Bulan Terakhir (Untuk Grafik) ---
        omset_6_bulan = []
        for i in range(5, -1, -1):
            target_month = now.month - i
            target_year = now.year
            if target_month <= 0:
                target_month += 12
                target_year -= 1
                
            # Tentukan rentang waktu untuk bulan target
            m_start = now.replace(year=target_year, month=target_month, day=1, hour=0, minute=0, second=0, microsecond=0)
            _, m_last_day = calendar.monthrange(target_year, target_month)
            m_end = now.replace(year=target_year, month=target_month, day=m_last_day, hour=23, minute=59, second=59, microsecond=999999)

            nama_bulan = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agt", "Sep", "Okt", "Nov", "Des"][target_month - 1]
            
            total = OrderItem.objects.filter(
                order__waktu__gte=m_start,
                order__waktu__lte=m_end
            ).aggregate(t=Sum('harga_jual'))['t'] or 0
            
            omset_6_bulan.append({
                'bulan': f"{nama_bulan}",
                'total': total
            })

        # --- Distribusi Status Order ---
        order_per_status = {
            s: Order.objects.filter(status_global=s).count()
            for s in ['review', 'desain', 'proses', 'ready', 'selesai', 'batal']
        }

        # --- Distribusi Status Job ---
        job_per_status = {
            s: JobBoard.objects.filter(status_pekerjaan=s).count()
            for s in ['antrean', 'dikerjakan', 'selesai', 'gagal', 'kendala']
        }

        # --- Top Staff (berdasarkan jumlah job selesai) ---
        top_staff_qs = CustomUser.objects.filter(role='staff').annotate(
            jumlah_job_selesai=Count(
                'my_tasks', filter=Q(my_tasks__status_pekerjaan='selesai')
            ),
            total_insentif=Sum(
                'my_tasks__insentif', filter=Q(my_tasks__status_pekerjaan='selesai')
            )
        ).order_by('-jumlah_job_selesai')[:5]

        top_staff = [
            {
                'nama': u.username,
                'jumlah_job_selesai': u.jumlah_job_selesai,
                'total_insentif': u.total_insentif or 0,
            }
            for u in top_staff_qs
        ]

        # --- Stok Kritis (stok di bawah minimum) ---
        stok_kritis_qs = InventoryItem.objects.filter(stok__lt=F('min_stok'))
        stok_kritis = [
            {
                'nama': item.nama,
                'stok': item.stok,
                'min_stok': item.min_stok,
                'satuan': item.satuan,
            }
            for item in stok_kritis_qs
        ]

        return Response({
            'total_order_hari_ini': total_order_hari_ini,
            'total_order_bulan_ini': total_order_bulan_ini,
            'omset_bulan_ini': omset_bulan_ini,
            'omset_6_bulan': omset_6_bulan,
            'order_per_status': order_per_status,
            'job_per_status': job_per_status,
            'top_staff': top_staff,
            'stok_kritis': stok_kritis,
        })


# ---------------------------------------------------------
# CREATE USER VIEW — Buat akun karyawan baru (Owner/Manager only)
# ---------------------------------------------------------
class CreateUserView(APIView):
    permission_classes = [IsOwnerOrManager]

    def post(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()
        role     = request.data.get('role', 'staff')
        no_hp    = request.data.get('no_hp', '')
        divisi   = request.data.get('divisi', None)
        first_name = request.data.get('first_name', '')

        # Validasi field wajib
        if not username or not password:
            return Response(
                {'error': 'Username dan password wajib diisi.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Cek duplikat username
        if CustomUser.objects.filter(username=username).exists():
            return Response(
                {'error': f'Username "{username}" sudah digunakan.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buat user baru
        user = CustomUser(
            username=username,
            role=role,
            no_hp=no_hp,
            first_name=first_name,
        )
        if divisi:
            from .models import Divisi as DivisiModel
            try:
                user.divisi = DivisiModel.objects.get(pk=divisi)
            except DivisiModel.DoesNotExist:
                pass

        user.set_password(password)  # Hash password dengan benar
        user.save()

        return Response(
            {
                'message': f'Akun "{username}" berhasil dibuat.',
                'id': user.id,
                'username': user.username,
                'role': user.role,
            },
            status=status.HTTP_201_CREATED
        )


# ---------------------------------------------------------
# FORWARD JOB VIEW — Teruskan job ke tahap/divisi selanjutnya
# ---------------------------------------------------------
class ForwardJobView(APIView):
    """
    POST /api/jobs/{job_id}/forward/
    Body:
      aksi        : 'forward' | 'selesai'
      tahap_id    : (wajib jika aksi='forward') ID TahapProses tujuan
      pic_staff_id: (opsional) ID staff untuk tahap baru
    """
    permission_classes = [IsAuthenticated, IsClockedIn]

    def post(self, request, job_id):
        # Ambil job
        try:
            job = JobBoard.objects.get(pk=job_id)
        except JobBoard.DoesNotExist:
            return Response({'error': 'Job tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        # Staff hanya bisa forward job miliknya
        if request.user.role == 'staff' and job.pic_staff != request.user:
            return Response({'error': 'Anda tidak memiliki akses ke job ini.'}, status=status.HTTP_403_FORBIDDEN)

        aksi         = request.data.get('aksi')          # 'forward' atau 'selesai'
        tahap_id     = request.data.get('tahap_id')
        pic_staff_id = request.data.get('pic_staff_id')

        # Guard: jangan proses job yang sudah selesai/gagal (mencegah duplikasi)
        if job.status_pekerjaan in ('selesai', 'gagal'):
            return Response(
                {'error': f'Job sudah berstatus "{job.status_pekerjaan}", tidak bisa diforward ulang.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            # Tandai job saat ini sebagai SELESAI
            job.status_pekerjaan = 'selesai'
            job.waktu_selesai    = timezone.now()
            job.otp_code         = ''
            job.otp_requested    = False
            job.otp_sent         = False
            job.save()

            # Potong bahan otomatis ke inventori
            deduct_job_materials_if_needed(job, request.user)

            if aksi == 'forward':
                if not tahap_id:
                    return Response(
                        {'error': 'tahap_id wajib diisi jika aksi=forward.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Ambil tahap tujuan
                try:
                    tahap_baru = TahapProses.objects.get(pk=tahap_id)
                except TahapProses.DoesNotExist:
                    return Response({'error': 'Tahap tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

                # Siapkan catatan dari divisi sebelumnya
                catatan_sebelumnya = job.catatan_staff if isinstance(job.catatan_staff, list) else []
                if catatan_sebelumnya or job.gdrive_output_link:
                    separator = {
                        "keterangan": f"--- Dari Divisi: {job.tahap.nama if job.tahap else 'Sebelumnya'} ---",
                        "qty": "-",
                        "satuan": "-",
                        "catatan": f"Oleh: {job.pic_staff.username if job.pic_staff else 'Staff'}",
                        "gdrive_link": job.gdrive_output_link or ""  # ← link file dari divisi sebelumnya
                    }
                    catatan_sebelumnya = catatan_sebelumnya + [separator]

                # Cek apakah sudah ada job untuk tahap ini di order item yang sama
                existing = JobBoard.objects.filter(
                    order_item=job.order_item, tahap=tahap_baru
                ).first()

                if existing:
                    # Reset job yang sudah ada ke antrean
                    existing.status_pekerjaan = 'antrean'
                    existing.waktu_mulai      = None
                    existing.waktu_selesai    = None
                    
                    # Gabung catatan lama dengan catatan dari divisi sebelumnya
                    existing_cat = existing.catatan_staff if isinstance(existing.catatan_staff, list) else []
                    existing.catatan_staff = existing_cat + catatan_sebelumnya
                    
                    if pic_staff_id:
                        try:
                            existing.pic_staff = CustomUser.objects.get(pk=pic_staff_id, role='staff')
                        except CustomUser.DoesNotExist:
                            pass
                    existing.save()
                    new_job_id = existing.id
                else:
                    # Buat job baru
                    new_job = JobBoard(
                        order_item      = job.order_item,
                        tahap           = tahap_baru,
                        status_pekerjaan = 'antrean',
                        catatan_staff   = catatan_sebelumnya
                    )
                    if pic_staff_id:
                        try:
                            new_job.pic_staff = CustomUser.objects.get(pk=pic_staff_id, role='staff')
                        except CustomUser.DoesNotExist:
                            pass
                    new_job.save()
                    new_job_id = new_job.id

                return Response({
                    'message': f'Job diteruskan ke tahap "{tahap_baru.nama}" (Divisi: {tahap_baru.divisi.nama}).',
                    'new_job_id': new_job_id,
                }, status=status.HTTP_201_CREATED)

            elif aksi == 'selesai':
                # Cek apakah seluruh job dari semua item dalam pesanan ini sudah selesai
                order = job.order_item.order
                active_jobs_exist = JobBoard.objects.filter(
                    order_item__order=order,
                    status_pekerjaan__in=['antrean', 'dikerjakan', 'kendala']
                ).exists()

                if not active_jobs_exist:
                    order.status_global = 'ready'
                    order.save()

                return Response(
                    {'message': 'Job ditandai selesai. Tidak ada tahap lanjutan.' + (' Order secara global telah siap diambil (READY).' if not active_jobs_exist else '')},
                    status=status.HTTP_200_OK
                )

            return Response({'error': 'Aksi tidak valid. Gunakan "forward" atau "selesai".'}, status=status.HTTP_400_BAD_REQUEST)

# ---------------------------------------------------------
# 12. WEBHOOK FONNTE (BOT WHATSAPP) — Full logic
# ---------------------------------------------------------

class FonnteWebhookView(APIView):
    """
    Endpoint webhook dari Fonnte. Tidak pakai JWT (AllowAny).
    Alur: tanya nama → tracking → form order → aturan awal → FAQ → AI
    """
    permission_classes = [AllowAny]

    def _kirim_balas(self, pesan):
        """Selalu kembalikan format array replies yang diharapkan Fonnte."""
        return Response({'replies': [{'message': pesan}]}, status=status.HTTP_200_OK)

    def _reply_kosong(self):
        """Untuk pesan yang diabaikan — tetap return replies array kosong."""
        return Response({'replies': []}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        from .wa_logic import (
            menunggu_nama,
            simpan_ke_memori, cek_tracking, cek_harga, cek_rules_awal,
            cek_database_faq, tanya_ai_finishing, ekstrak_nama_dari_pesan,
        )

        data = request.data

        # Fonnte kadang kirim data nested di bawah key 'query'
        if 'query' in data and isinstance(data['query'], dict):
            sender  = str(data['query'].get('sender', '')).strip()
            message = str(data['query'].get('message', '')).strip()
            is_group = data['query'].get('isGroup', False)
        else:
            sender  = str(data.get('sender', '')).strip()
            message = str(data.get('message', data.get('query', ''))).strip()
            is_group = False

        # Bersihkan format nomor: hapus +, spasi, tanda -
        # Contoh: "+62 882-0075-63131" → "628820075631131"
        sender_bersih = sender.replace('+', '').replace(' ', '').replace('-', '')

        if not message or not sender_bersih:
            return self._reply_kosong()

        # Abaikan pesan dari grup WhatsApp
        if is_group or '@g.us' in sender:
            return self._reply_kosong()

        sender = sender_bersih  # pakai nomor yang sudah bersih

        # Ambil kontak dari DB
        contact_obj    = Contact.objects.filter(nomor_wa=sender).first()
        nama_pelanggan = contact_obj.nama if contact_obj else ""
        p_kecil        = message.lower()
        panggilan      = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

        jawaban = ""

        # ── STEP 1: Tanya nama (kontak baru) ──────────────────────
        if not nama_pelanggan and sender not in menunggu_nama:
            menunggu_nama.add(sender)
            try:
                biz_name = SystemConfig.objects.get(key='bisnis_nama').value or 'Brandy'
            except Exception:
                biz_name = 'Brandy'
            jawaban = (
                f"Halo Kak! 👋 Selamat datang di *{biz_name}*.\n"
                "Boleh tahu dengan Kakak siapa ini biar lebih enak ngobrolnya? 😊"
            )
            return self._kirim_balas(jawaban)

        elif sender in menunggu_nama:
            # Ekstrak nama bersih dari jawaban (bisa berupa kalimat)
            nama_baru = ekstrak_nama_dari_pesan(message)
            contact_obj, _ = Contact.objects.get_or_create(
                nomor_wa=sender, defaults={'nama': nama_baru}
            )
            if not contact_obj.nama:
                contact_obj.nama = nama_baru
                contact_obj.save()
            elif contact_obj.nama != nama_baru:
                contact_obj.nama = nama_baru
                contact_obj.save()
            menunggu_nama.discard(sender)
            nama_pelanggan = nama_baru
            panggilan      = f"Kak {nama_pelanggan}"
            jawaban = (
                f"Salam kenal {panggilan}! ✨\n"
                f"Ada yang bisa kami bantu hari ini? Mau cetak apa nih Kak?"
            )
            return self._kirim_balas(jawaban)

        # Simpan ke memori AI
        simpan_ke_memori(sender, "user", message, nama_pelanggan)

        # ── STEP 2: Tracking pesanan ───────────────────────────────
        jawaban = cek_tracking(message, sender, nama_pelanggan)
        if jawaban:
            # Langsung balas tracking — jangan lanjut ke step lain
            simpan_ke_memori(sender, "assistant", jawaban, nama_pelanggan)
            return self._kirim_balas(jawaban)

        # ── STEP 3: Deteksi form order / desain masuk ─────────────
        if not jawaban:
            # Deteksi form order (template lama & baru)
            is_form_order = (
                # Template baru: ada "jenis produk" + "no. wa" atau "item 1"
                ('jenis produk' in p_kecil and ('no. wa' in p_kecil or 'item 1' in p_kecil or 'no wa' in p_kecil))
                or
                # Template lama: ada "nama pemesan" + "jenis produk"
                ('nama pemesan' in p_kecil and 'jenis produk' in p_kecil)
            )
            is_form_desain = 'tulisan yang dimuat' in p_kecil or 'dominan warna' in p_kecil

            if is_form_order or is_form_desain:
                if 'data sudah sesuai' in p_kecil:
                    detail_bersih = _re.sub(r'(?i)\*?data sudah sesuai\*?', '', message).strip()
                    order_id = self._simpan_order_dari_form(sender, nama_pelanggan, detail_bersih)

                    label = "Konsep desain sudah masuk ke Antrean Desain" if is_form_desain else "Pesanan Anda telah masuk ke sistem kami"
                    jawaban = (
                        f"Terima kasih {panggilan}! {label} ✅\n\n"
                        f"🎫 *ID PESANAN: {order_id}*\n"
                        f"_Simpan ID ini untuk melacak status pesanan Kakak._\n\n"
                        f"Tim kami akan segera memverifikasi pesanan Kakak. Mohon ditunggu 🙏"
                    )
                else:
                    jawaban = (
                        f"Terima kasih {panggilan}! Data order sudah kami terima.\n\n"
                        f"⚠️ *PENTING:*\n"
                        f"Jika data di atas sudah benar, silakan *copy-paste ulang* form pesanan "
                        f"tersebut dan tambahkan tulisan *DATA SUDAH SESUAI* di baris paling bawah "
                        f"agar pesanan Kakak langsung otomatis terdaftar di sistem kami ya. 🙏😊"
                    )

        # ── STEP 4: Cek tanya harga (jawab info harga, TANPA form) ──
        if not jawaban:
            jawaban = cek_harga(message, nama_pelanggan)

        # ── STEP 5: Aturan awal (sapaan, katalog, minta form) ─────
        if not jawaban:
            jawaban = cek_rules_awal(message, sender, nama_pelanggan)

        # ── STEP 6: FAQ dari database ──────────────────────────────
        if not jawaban:
            jawaban = cek_database_faq(message, nama_pelanggan)

        # ── STEP 7: AI Fallback (KoboiLLM / Gemini) ───────────────
        if not jawaban:
            jawaban = tanya_ai_finishing(sender)

        simpan_ke_memori(sender, "assistant", jawaban, nama_pelanggan)
        return self._kirim_balas(jawaban)

    def _simpan_order_dari_form(self, nomor, nama_kontak, detail):
        """
        Parse teks form WA → simpan ke DB.
        Support multi-item: tiap blok 'Item N' jadi 1 OrderItem terpisah.
        Kembalikan order_id.
        """

        def ambil_field(teks, *keys):
            for key in keys:
                # Pakai [^\n]* agar tidak match lintas baris
                match = _re.search(
                    rf'-\s*{_re.escape(key)}\s*:\s*([^\n]*)',
                    teks, _re.IGNORECASE
                )
                if match:
                    val = match.group(1).strip().strip('*_')
                    if val and val not in ('-', 'sudah ada / belum ada', '*sudah ada* / *belum ada*'):
                        return val
            return ''

        # Ambil nama pemesan dari form (bisa "Nama Pemesan" atau "Nama")
        nama_dari_form = (
            ambil_field(detail, 'Nama Pemesan', 'Nama') or nama_kontak or '-'
        )

        # ── Pisah per blok item ──────────────────────────────────
        # Cari semua penanda item: "Item 1", "Item 2", dst.
        # Regex tidak bergantung emoji agar lebih reliable
        blok_items = _re.split(r'(?im)^\s*[\*_]*\s*(?:📦\s*)?[\*_]*item\s+\d+[\*_]*\s*[\*_]*.*$', detail)
        blok_items = [b.strip() for b in blok_items if b.strip()]

        if len(blok_items) <= 1:
            # Tidak ada penanda item → seluruh teks jadi 1 item
            blok_items = [detail]

        # Tentukan nama dari kontak
        nama_order = nama_dari_form

        with transaction.atomic():
            contact, _ = Contact.objects.get_or_create(
                nomor_wa=nomor, defaults={'nama': nama_kontak}
            )
            # BUG FIX: last_order adalah DateField, gunakan localdate() bukan strftime dengan jam:menit
            existing_orders = Order.objects.filter(nomor_wa=nomor)
            contact.total_order = existing_orders.count() + 1
            contact.total_spent = sum(
                item.harga_jual
                for o in existing_orders.prefetch_related('items')
                for item in o.items.all()
            )
            contact.last_order  = timezone.localdate()
            contact.save()

            order_id = f"ORD-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
            order = Order.objects.create(
                id=order_id,
                nomor_wa=contact.nomor_wa,  # BUG FIX: harus string, bukan objek Contact
                nama=nama_order,
                status_global='review',
                catatan_pelanggan=detail[:500],
            )

            items_dibuat = 0
            for blok in blok_items:
                if not blok.strip():
                    continue

                jenis_produk = ambil_field(blok, 'Jenis Produk') or 'Umum'
                jumlah_str   = ambil_field(blok, 'Jumlah')
                ukuran       = ambil_field(blok, 'Ukuran')
                bahan        = ambil_field(blok, 'Bahan/Material', 'Bahan / Material', 'Bahan')
                finishing    = ambil_field(blok, 'Finishing')
                file_desain  = ambil_field(blok, 'File Desain').lower()
                keterangan   = ambil_field(blok, 'Keterangan')

                # Skip blok item kosong (Item 2 yang tidak diisi)
                if jenis_produk == 'Umum' and not ukuran and not bahan:
                    continue

                try:
                    qty = int(''.join(filter(str.isdigit, jumlah_str)) or '1')
                except Exception:
                    qty = 1

                detail_produk = " | ".join(filter(None, [ukuran, bahan, finishing, keterangan]))

                gdrive_link = ''
                link_match = _re.search(r'(https?://\S+)', blok)
                if link_match:
                    gdrive_link = link_match.group(1)

                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk=jenis_produk,
                    qty=qty,
                    harga_jual=0,
                    detail=detail_produk,
                    gdrive_customer_link=gdrive_link,
                )

                # Tentukan tahap awal
                if 'belum' in file_desain:
                    tahap_awal = TahapProses.objects.filter(
                        nama__icontains='desain'
                    ).order_by('urutan').first()
                else:
                    tahap_awal = TahapProses.objects.order_by('urutan').first()

                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )
                items_dibuat += 1

            # Jika tidak ada item yang terdeteksi, buat 1 item generik
            if items_dibuat == 0:
                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk='Umum',
                    qty=1,
                    harga_jual=0,
                    detail=detail[:200],
                )
                tahap_awal = TahapProses.objects.order_by('urutan').first()
                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )


        return order_id


# ---------------------------------------------------------
# STAFF PERFORMANCE REPORT VIEW — Agregasi kinerja karyawan
# ---------------------------------------------------------
class StaffPerformanceReportView(APIView):
    """
    GET /api/reports/staff-performance/
    Mengembalikan statistik kinerja masing-masing staff:
    - Jumlah job diselesaikan, sedang berjalan, gagal, dll.
    - Total insentif yang diperoleh.
    - Rata-rata durasi penyelesaian tugas (menit).
    """
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        import calendar
        from django.utils import timezone
        
        time_range = request.query_params.get('range', 'bulan_ini')
        now = timezone.localtime(timezone.now())
        
        # Default rentang waktu
        start = None
        end = None
        
        if time_range == 'bulan_ini':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            _, last_day = calendar.monthrange(now.year, now.month)
            end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
        elif time_range == 'bulan_lalu':
            year = now.year
            month = now.month - 1
            if month <= 0:
                month = 12
                year -= 1
            start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
            _, last_day = calendar.monthrange(year, month)
            end = now.replace(year=year, month=month, day=last_day, hour=23, minute=59, second=59, microsecond=999999)

        # Ambil semua user dengan role staff (dioptimasi dengan prefetch_related)
        staff_members = CustomUser.objects.filter(role='staff').select_related('divisi').prefetch_related('my_tasks', 'absensi')
        
        from hr.models import DailyAttendanceSession
        if start and end:
            sessions = DailyAttendanceSession.objects.filter(tanggal__gte=start.date(), tanggal__lte=end.date())
        else:
            sessions = DailyAttendanceSession.objects.all()
        session_map = {s.tanggal: s.batas_maksimal for s in sessions}

        report_data = []
        for staff in staff_members:
            jobs = staff.my_tasks.all()
            
            # Saring berdasarkan rentang waktu jika diset
            if start and end:
                completed_jobs = jobs.filter(status_pekerjaan='selesai', waktu_selesai__gte=start, waktu_selesai__lte=end)
                failed_jobs = jobs.filter(status_pekerjaan__in=['gagal', 'batal'], waktu_selesai__gte=start, waktu_selesai__lte=end)
            else:
                completed_jobs = jobs.filter(status_pekerjaan='selesai')
                failed_jobs = jobs.filter(status_pekerjaan__in=['gagal', 'batal'])
                
            active_jobs = jobs.filter(status_pekerjaan='dikerjakan')
            pending_jobs = jobs.filter(status_pekerjaan='antrean')
            constraint_jobs = jobs.filter(status_pekerjaan='kendala')

            total_jobs = completed_jobs.count() + failed_jobs.count() + active_jobs.count() + pending_jobs.count() + constraint_jobs.count()
            total_insentif = sum(j.insentif for j in completed_jobs)
            
            # Rata-rata durasi pengerjaan job selesai (menit)
            durations = []
            for j in completed_jobs:
                if j.waktu_mulai and j.waktu_selesai:
                    diff = j.waktu_selesai - j.waktu_mulai
                    durations.append(diff.total_seconds() / 60.0)
            
            avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

            # Hitung statistik kehadiran
            absensi_list = staff.absensi.all()
            if start and end:
                absensi_list = [a for a in absensi_list if start.date() <= a.tanggal <= end.date()]
            
            ontime_count = 0
            late_count = 0
            alpha_count = 0
            
            for a in absensi_list:
                if a.status == 'alpha':
                    alpha_count += 1
                elif a.status in ['hadir', 'wfh', 'izin']:
                    batas = session_map.get(a.tanggal)
                    if batas and a.jam_masuk:
                        if a.jam_masuk > batas:
                            late_count += 1
                        else:
                            ontime_count += 1
                    else:
                        if a.status == 'izin':
                            late_count += 1
                        else:
                            ontime_count += 1
            
            report_data.append({
                'id': staff.id,
                'username': staff.username,
                'nama_lengkap': staff.get_full_name() or staff.username,
                'divisi': staff.divisi.nama if staff.divisi else '-',
                'jobs_total': total_jobs,
                'jobs_completed': completed_jobs.count(),
                'jobs_in_progress': active_jobs.count(),
                'jobs_pending': pending_jobs.count(),
                'jobs_failed': failed_jobs.count(),
                'jobs_constraint': constraint_jobs.count(),
                'total_insentif': total_insentif,
                'avg_duration_minutes': avg_duration,
                'att_ontime': ontime_count,
                'att_late': late_count,
                'att_alpha': alpha_count,
                'att_total': len(absensi_list)
            })
            
        # Detailed Jobs
        jobs_qs = JobBoard.objects.select_related(
            'order_item', 'order_item__order', 'tahap', 'tahap__divisi', 'pic_staff'
        ).order_by('-waktu_selesai')
        
        if start and end:
            jobs_qs = jobs_qs.filter(waktu_selesai__gte=start, waktu_selesai__lte=end)
        else:
            jobs_qs = jobs_qs.filter(waktu_selesai__isnull=False)[:500]

        detailed_jobs = []
        for j in jobs_qs:
            duration_minutes = 0
            if j.waktu_mulai and j.waktu_selesai:
                diff = j.waktu_selesai - j.waktu_mulai
                duration_minutes = round(diff.total_seconds() / 60.0, 1)

            detailed_jobs.append({
                'id': j.id,
                'order_id': j.order_item.order.id if j.order_item and j.order_item.order else '-',
                'order_nama': j.order_item.order.nama if j.order_item and j.order_item.order else '-',
                'order_tgl': j.order_item.order.waktu.strftime('%Y-%m-%d %H:%M') if j.order_item and j.order_item.order else '-',
                'jenis_produk': j.order_item.jenis_produk if j.order_item else '-',
                'bahan': j.order_item.bahan if j.order_item else '-',
                'qty': j.order_item.qty if j.order_item else 1,
                'tahap': j.tahap.nama if j.tahap else '-',
                'divisi': j.tahap.divisi.nama if j.tahap and j.tahap.divisi else '-',
                'pic_username': j.pic_staff.username if j.pic_staff else 'Unassigned',
                'pic_fullname': j.pic_staff.get_full_name() or j.pic_staff.username if j.pic_staff else 'Unassigned',
                'status': j.status_pekerjaan,
                'waktu_mulai': j.waktu_mulai.strftime('%Y-%m-%d %H:%M') if j.waktu_mulai else '-',
                'waktu_selesai': j.waktu_selesai.strftime('%Y-%m-%d %H:%M') if j.waktu_selesai else '-',
                'durasi_menit': duration_minutes,
                'insentif': j.insentif,
                'biaya_desain': j.biaya_desain
            })

        return Response({
            'range': time_range,
            'data': report_data,
            'detailed_jobs': detailed_jobs
        })