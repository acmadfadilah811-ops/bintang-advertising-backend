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
import logging
from .models import (

    Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard,
    InventoryItem, RestockHistory, ProductPrice, SystemConfig, FAQ,
    OrderActivityLog, KomplainOrder, KomplainLog, CustomerActivity,
    BillOfMaterials, BoMItem
)
from .serializers import (
    DivisiSerializer, TahapProsesSerializer, CustomUserSerializer,
    ContactSerializer, OrderSerializer, OrderItemSerializer, JobBoardSerializer,
    InventoryItemSerializer, ProductPriceSerializer, SystemConfigSerializer, FAQSerializer,
    BusinessSettingsSerializer, KomplainOrderSerializer, KomplainLogSerializer,
    CustomerActivitySerializer, BillOfMaterialsSerializer, BoMItemSerializer
)
import os
import calendar
from django.core.cache import cache
from django.db.models import OuterRef, Subquery, Max
from django.db.models.functions import Coalesce
from hr.models import Akun, TransaksiBukuBesar
from users.models import SecurityAuditLog
from .whatsapp_client import whatsapp_client

logger = logging.getLogger(__name__)

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
        # TEMPORARY BYPASS: Selalu True untuk pengetesan/review log Papan Kerja
        return True

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

    def check_permissions(self, request):
        super().check_permissions(request)
        # Proteksi hak akses: Staff tidak boleh mengubah data karyawan lain
        # Hanya Owner, Manager, atau Admin yang dapat memodifikasi model karyawan secara umum
        if request.method not in ['GET', 'HEAD', 'OPTIONS']:
            if self.action != 'me':
                if not (request.user and getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']):
                    self.permission_denied(request, message="Hanya Owner, Manager, atau Admin yang dapat memodifikasi data karyawan.")

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
            # Proteksi field HR/Admin agar tidak bisa diubah oleh staff secara mandiri
            if hasattr(request.data, '_mutable'):
                data = request.data.copy()
            elif isinstance(request.data, dict):
                data = request.data.copy()
            else:
                data = dict(request.data)

            if request.user.role not in ['owner', 'manager', 'admin']:
                hr_fields = [
                    'username', 'email', 'role', 'divisi', 'status_karyawan', 
                    'jenis_kontrak', 'kontrak_mulai', 'kontrak_selesai', 
                    'no_kpj', 'bpjs_kes', 'file_pkwt', 'nip'
                ]
                for field in hr_fields:
                    if field in data:
                        data.pop(field)

            serializer = self.get_serializer(user, data=data, partial=True)
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
        from django.db.models import Max, Sum, Count

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
        from django.db.models import Sum, Avg, Count
        
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
        today    = timezone.now().strftime('%Y%m%d')
        short_id = uuid.uuid4().hex[:4].upper()
        inv_id   = f'INV-{today}-{short_id}'
        serializer.save(id=inv_id)

    def update(self, request, *args, **kwargs):
        # Mencegah modifikasi stok manual saat update item
        if 'stok' in request.data:
            instance = self.get_object()
            try:
                new_stok = float(request.data['stok'])
                if abs(new_stok - float(instance.stok)) > 0.0001:
                    return Response(
                        {"error": "Stok tidak dapat diubah secara manual pada menu edit. Gunakan tombol 'Restock' atau 'Penyesuaian Stok' agar riwayat mutasi tercatat."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                pass
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)




# ---------------------------------------------------------
# RESTOCK VIEW — Standalone APIView (lebih reliable dari @action)
# ---------------------------------------------------------
class InventoryRestockView(APIView):
    """POST /api/inventory/<pk>/restock/ — Tambah/kurangi stok dan catat history."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
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

        # ✅ FIX: Gunakan select_for_update() + transaction.atomic() untuk
        # mencegah race condition ketika ada 2+ request bersamaan mengubah stok
        with transaction.atomic():
            item = InventoryItem.objects.select_for_update().get(pk=pk)
            stok_awal  = item.stok
            stok_akhir = max(0.0, item.stok + delta)

            item.stok = stok_akhir
            item.save()

            RestockHistory.objects.create(
                item       = item,
                user       = request.user,
                delta      = delta,
                stok_awal  = stok_awal,
                stok_akhir = stok_akhir,
                keterangan = keterangan,
            )

        return Response({
            'ok':       True,
            'id':       item.id,
            'nama':     item.nama,
            'stok_baru': stok_akhir,
        }, status=status.HTTP_200_OK)


def record_material_consumption_to_general_ledger(inventory_item, qty, job):
    """
    Mencatat konsumsi bahan baku ke Buku Besar (Double-Entry Bookkeeping) sebagai Beban HPP.
    """
    try:
        
        # Hitung nilai HPP (kuantitas * cost_per_unit)
        cost = qty * (inventory_item.cost_per_unit or 0.0)
        if cost <= 0:
            return
            
        akun_hpp, _ = Akun.objects.get_or_create(
            kode_akun='5-1000',
            defaults={'nama_akun': 'Beban Bahan Baku (HPP)', 'kategori': 'Beban'}
        )
        akun_persediaan, _ = Akun.objects.get_or_create(
            kode_akun='1-3000',
            defaults={'nama_akun': 'Persediaan Bahan Baku', 'kategori': 'Aset'}
        )
        
        ref_no = f"Job #{job.id}"
        order_id = job.order_item.order.id
        ket_tx = f"HPP Otomatis: {inventory_item.nama} ({qty} {inventory_item.satuan}) - Order {order_id} - Job {job.id}"
        
        # DEBIT ke akun Beban HPP (Beban bertambah)
        TransaksiBukuBesar.objects.create(
            akun=akun_hpp,
            tanggal=timezone.localdate(),
            no_referensi=ref_no,
            keterangan=ket_tx,
            debit=int(round(cost)),
            kredit=0
        )
        
        # KREDIT ke akun Persediaan (Aset berkurang)
        TransaksiBukuBesar.objects.create(
            akun=akun_persediaan,
            tanggal=timezone.localdate(),
            no_referensi=ref_no,
            keterangan=ket_tx,
            debit=0,
            kredit=int(round(cost))
        )
    except Exception as e:
        logger.error(f"Gagal mencatat jurnal HPP otomatis untuk Job #{getattr(job, 'id', '?')}: {e}", exc_info=True)


def deduct_job_materials_if_needed(job, user):
    """
    Mengurangi stok bahan baku. Prioritas pertama menggunakan sistem Bill of Materials (BoM).
    Jika BoM tidak ditemukan untuk produk/bahan terkait, fallback ke input manual di catatan_staff.
    Juga mencatat konsumsi bahan ke Buku Besar (HPP).
    """
    from .models import InventoryItem, RestockHistory, ProductPrice, BillOfMaterials, BoMItem
    
    marker = f"Job #{job.id}"
    if RestockHistory.objects.filter(keterangan__icontains=marker).exists():
        return
        
    order_item = job.order_item
    
    # 1. Cari ProductPrice & BoM
    product = ProductPrice.objects.filter(nama_produk=order_item.jenis_produk, material=order_item.bahan).first()
    if not product:
        product = ProductPrice.objects.filter(nama_produk=order_item.jenis_produk).first()
        
    bom = None
    if product:
        bom = BillOfMaterials.objects.filter(product=product).first()
        
    if bom:
        # Gunakan pemotongan otomatis berbasis BoM
        with transaction.atomic():
            for bom_item in bom.items.all():
                item = bom_item.inventory_item
                # Lock item for update
                item = InventoryItem.objects.select_for_update().get(pk=item.pk)
                
                luas = order_item.luas
                if luas > 0:
                    qty_needed = round(luas * order_item.qty * bom_item.qty_required_per_unit, 4)
                else:
                    qty_needed = round(order_item.qty * bom_item.qty_required_per_unit, 4)
                    
                if qty_needed <= 0:
                    continue
                    
                stok_awal = item.stok
                stok_akhir = max(0.0, round(item.stok - qty_needed, 4))
                
                RestockHistory.objects.create(
                    item=item,
                    user=user,
                    delta=-qty_needed,
                    stok_awal=stok_awal,
                    stok_akhir=stok_akhir,
                    keterangan=f"Pemakaian BoM otomatis | Job #{job.id} | {bom.nama}",
                )
                
                item.stok = stok_akhir
                item.save()
                
                # Catat ke Buku Besar
                record_material_consumption_to_general_ledger(item, qty_needed, job)
        return

    # 2. Fallback: Gunakan pemotongan manual dari catatan_staff
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
            
            # Catat ke Buku Besar
            record_material_consumption_to_general_ledger(item, qty, job)



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


def record_payment_to_general_ledger(order, jumlah_bayar, metode, is_dp=False):
    """
    Mencatat pembayaran (DP / Pelunasan) secara otomatis ke Buku Besar (Double-Entry Bookkeeping).
    """
    try:
        
        metode_clean = (metode or 'tunai').lower()
        if metode_clean == 'transfer':
            kode_aset = '1-1001'
            nama_aset = 'Bank Transfer'
        elif metode_clean == 'qris':
            kode_aset = '1-1002'
            nama_aset = 'QRIS / E-Wallet'
        else:
            kode_aset = '1-1000'
            nama_aset = 'Kas Tunai'
            
        akun_aset, _ = Akun.objects.get_or_create(
            kode_akun=kode_aset,
            defaults={'nama_akun': nama_aset, 'kategori': 'Aset'}
        )
        akun_pendapatan, _ = Akun.objects.get_or_create(
            kode_akun='4-1000',
            defaults={'nama_akun': 'Pendapatan Jasa Cetak', 'kategori': 'Pendapatan'}
        )
        
        ref_no = f"Order #{order.id}"
        tipe_bayar = "DP" if is_dp else "Pelunasan"
        ket_tx = f"Pembayaran {tipe_bayar} {metode_clean.upper()} - Pelanggan: {order.nama} ({ref_no})"
        
        # DEBIT ke akun Aset (Kas/Bank bertambah)
        TransaksiBukuBesar.objects.create(
            akun=akun_aset,
            tanggal=timezone.localdate(),
            no_referensi=ref_no,
            keterangan=ket_tx,
            debit=jumlah_bayar,
            kredit=0
        )
        
        # KREDIT ke akun Pendapatan (Pendapatan bertambah)
        TransaksiBukuBesar.objects.create(
            akun=akun_pendapatan,
            tanggal=timezone.localdate(),
            no_referensi=ref_no,
            keterangan=ket_tx,
            debit=0,
            kredit=jumlah_bayar
        )
    except Exception as e:
        logger.error(f"Gagal mencatat jurnal Buku Besar otomatis untuk Order #{getattr(order, 'id', '?')}: {e}", exc_info=True)


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
        
        # ✅ Filter by nomor_wa if provided in query params (optimasi query detail customer)
        nomor_wa = self.request.query_params.get('nomor_wa')
        if nomor_wa:
            base_qs = base_qs.filter(nomor_wa=nomor_wa)
            
        # ✅ Filter search di database level
        search = self.request.query_params.get('search')
        if search:
            from django.db.models import Q
            base_qs = base_qs.filter(
                Q(id__icontains=search) |
                Q(nama__icontains=search) |
                Q(nomor_wa__icontains=search)
            )
            
        # ✅ Filter tab/status di database level
        tab = self.request.query_params.get('tab')
        if tab:
            if tab == 'piutang':
                base_qs = base_qs.filter(sisa_tagihan__gt=0).exclude(status_global='batal')
            elif tab in ['draft', 'quotation', 'review', 'desain', 'proses', 'ready', 'selesai', 'batal']:
                base_qs = base_qs.filter(status_global=tab)
            
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
        instance = serializer.save(id=order_id, _current_user=self.request.user)
        
        # Log pembuatan pesanan
        OrderActivityLog.objects.create(
            order=instance,
            user=self.request.user,
            tindakan="CREATE_ORDER",
            keterangan=f"Pesanan baru '{instance.id}' berhasil dibuat."
        )

        # Buku Besar Otomatis jika ada DP awal
        if instance.dp_dibayar > 0:
            record_payment_to_general_ledger(
                order=instance,
                jumlah_bayar=instance.dp_dibayar,
                metode=getattr(instance, 'metode_pembayaran', 'tunai'),
                is_dp=True
            )

    def perform_update(self, serializer):
        serializer.instance._current_user = self.request.user
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        # Proteksi Keamanan: Hanya Owner dan Manager yang boleh menghapus pesanan
        if request.user.role not in ['owner', 'manager']:
            SecurityAuditLog.objects.create(
                user=request.user,
                event="PERMISSION_DENIED",
                ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan=f"Ditolak menghapus pesanan {kwargs.get('pk')} karena hak akses tidak mencukupi (role: {request.user.role})",
                berhasil=False,
            )
            return Response({'error': 'Hanya Owner atau Manager yang diperbolehkan menghapus pesanan secara permanen.'}, status=status.HTTP_403_FORBIDDEN)
        
        order = self.get_object()
        # Catat audit log penghapusan sebelum dihapus
        SecurityAuditLog.objects.create(
            user=request.user,
            event="TOKEN_REVOKED", # Menggunakan token_revoked sebagai representasi general admin revoke action
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
            keterangan=f"Berhasil menghapus permanen pesanan '{order.id}' milik pelanggan '{order.nama}'",
            berhasil=True,
        )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/orders/stats/
        Mendapatkan data statistik ringkasan pesanan untuk dashboard pesanan secara cepat dari database.
        """
        user = self.request.user
        base_qs = Order.objects.all()
        
        # Sesuai get_queryset, batasi data jika bukan admin/owner/manager
        if user.role not in ['owner', 'manager', 'admin']:
            my_order_ids = JobBoard.objects.filter(
                pic_staff=user
            ).values_list('order_item__order_id', flat=True)
            base_qs = base_qs.filter(id__in=my_order_ids)
            
        from django.db.models import Count, Sum, Q
        stats_agg = base_qs.aggregate(
            total_count=Count('id'),
            total_piutang=Sum('sisa_tagihan'),
            piutang_count=Count('id', filter=Q(sisa_tagihan__gt=0) & ~Q(status_global='batal'))
        )
        
        # Kelompokkan berdasarkan status_global
        status_counts = base_qs.values('status_global').annotate(count=Count('id'))
        status_map = {item['status_global']: item['count'] for item in status_counts}
        
        # Hitung sisa tagihan khusus yang tidak batal
        piutang_non_batal = base_qs.exclude(status_global='batal').aggregate(
            total=Sum('sisa_tagihan')
        )['total'] or 0
        
        return Response({
            'total_count': stats_agg['total_count'] or 0,
            'total_piutang': piutang_non_batal,
            'piutang_count': stats_agg['piutang_count'] or 0,
            'draft': status_map.get('draft', 0),
            'quotation': status_map.get('quotation', 0),
            'review': status_map.get('review', 0),
            'desain': status_map.get('desain', 0),
            'proses': status_map.get('proses', 0),
            'ready': status_map.get('ready', 0),
            'selesai': status_map.get('selesai', 0),
            'batal': status_map.get('batal', 0),
        })

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
        order._current_user = request.user
        order.save()

        # Buku Besar Otomatis untuk Pelunasan / Cicilan
        record_payment_to_general_ledger(
            order=order,
            jumlah_bayar=jumlah_bayar,
            metode=metode,
            is_dp=False
        )

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
        try:
            biaya_desain = int(request.data.get('biaya_desain', 0) or 0)
        except (ValueError, TypeError):
            biaya_desain = 0
        try:
            insentif = int(request.data.get('insentif', 0) or 0)
        except (ValueError, TypeError):
            insentif = 0

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
                tahap = TahapModel.objects.filter(divisi_id=divisi_id).order_by('urutan').first()
            except Exception:
                pass

        # JIKA tahap masih None tapi staff diisi, ambil tahap pertama divisi staff sebagai fallback
        if not tahap and staff and staff.divisi:
            try:
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
        target_name = staff.username if staff else (tahap.divisi.nama if tahap and tahap.divisi else 'Divisi')
        tahap_desc = tahap.nama if tahap else "Tahap Awal"
        
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
            
            # Log penugasan/penerbitan SPK
            action_desc = "TUGASKAN_STAFF" if staff else "TERBITKAN_SPK"
            OrderActivityLog.objects.create(
                order=order,
                user=request.user,
                tindakan=action_desc,
                keterangan=f"Menerbitkan SPK item '{item.jenis_produk}' ke {target_name} untuk tahap '{tahap_desc}'"
            )

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
        
        order._current_user = request.user
        order.save()

        return Response({
            'message': f'Order {order_id} berhasil di-publish/assign ke {target_name}.',
            'jobs': created_jobs,
        }, status=status.HTTP_200_OK)

class OrderItemViewSet(viewsets.ModelViewSet):
    queryset = OrderItem.objects.select_related(
        'order'
    ).order_by('-id')
    serializer_class = OrderItemSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(_current_user=self.request.user)

    def perform_update(self, serializer):
        serializer.instance._current_user = self.request.user
        serializer.save()

    def perform_destroy(self, instance):
        instance._current_user = self.request.user
        instance.delete()

class JobBoardViewSet(viewsets.ModelViewSet):
    serializer_class = JobBoardSerializer
    permission_classes = [IsAuthenticated, IsClockedIn]

    def perform_update(self, serializer):
        # Ambil status sebelum update
        old_instance = self.get_object()
        old_status = old_instance.status_pekerjaan
        new_status = serializer.validated_data.get('status_pekerjaan', old_status)

        # ✅ FIX: Validasi status transition — mencegah status lompat tidak valid
        VALID_TRANSITIONS = {
            'antrean':   ['dikerjakan', 'kendala', 'gagal', 'batal', 'antrean'],
            'dikerjakan':['selesai', 'kendala', 'gagal', 'batal', 'antrean', 'dikerjakan'],
            'kendala':   ['dikerjakan', 'antrean', 'gagal', 'batal', 'kendala'],
            'selesai':   [],  # Status final — tidak bisa diubah lagi
            'gagal':     ['antrean'],  # Bisa di-retry dari gagal
            'batal':     [],  # Status final
        }
        allowed = VALID_TRANSITIONS.get(old_status, [])
        if new_status != old_status and new_status not in allowed:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            raise DRFValidationError(
                {"status_pekerjaan": f"Transisi status '{old_status}' → '{new_status}' tidak diizinkan."}
            )
        
        # Simpan pembaruan
        serializer.instance._current_user = self.request.user
        job = serializer.save()
        new_status = job.status_pekerjaan
        
        # Jalankan logika sinkronisasi jika status berubah
        if old_status != new_status:
            user = self.request.user
            
            # 1. Kembali ke Antrean
            if new_status == 'antrean':
                job.waktu_mulai = None
                job.waktu_selesai = None
                job.save()
                
                # Catat OrderActivityLog
                tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
                OrderActivityLog.objects.create(
                    order=job.order_item.order,
                    user=user,
                    tindakan="RESET_JOB",
                    keterangan=f"Pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}' dikembalikan ke Antrean"
                )
            
            # 2. Mulai Dikerjakan
            elif new_status == 'dikerjakan':
                if not job.waktu_mulai:
                    job.waktu_mulai = timezone.now()
                job.waktu_selesai = None
                job.save()
                
                # Catat OrderActivityLog
                tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
                OrderActivityLog.objects.create(
                    order=job.order_item.order,
                    user=user,
                    tindakan="START_JOB",
                    keterangan=f"Staff '{user.username}' mulai memproses pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}'"
                )
                
            # 3. Selesai Sukses
            elif new_status == 'selesai':
                if not job.waktu_selesai:
                    job.waktu_selesai = timezone.now()
                # Reset OTP fields
                job.otp_code = ''
                job.otp_requested = False
                job.otp_sent = False
                job.save()
                
                # Catat OrderActivityLog
                tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
                OrderActivityLog.objects.create(
                    order=job.order_item.order,
                    user=user,
                    tindakan="COMPLETE_JOB",
                    keterangan=f"Staff '{user.username}' berhasil menyelesaikan pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}'"
                )
                
                # Potong stok bahan otomatis
                deduct_job_materials_if_needed(job, user)
                
                # Cek kelengkapan pesanan secara global
                order = job.order_item.order
                active_jobs_exist = JobBoard.objects.filter(
                    order_item__order=order,
                    status_pekerjaan__in=['antrean', 'dikerjakan', 'kendala']
                ).exists()
                if not active_jobs_exist:
                    order.status_global = 'ready'
                    order._current_user = user
                    order.save()
                    
                    OrderActivityLog.objects.create(
                        order=order,
                        user=user,
                        tindakan="READY_ORDER",
                        keterangan="Semua item selesai diproduksi. Status pesanan diubah otomatis menjadi 'Siap Diambil'."
                    )
            
            # 4. Gagal / Batal
            elif new_status in ('gagal', 'batal'):
                if not job.waktu_selesai:
                    job.waktu_selesai = timezone.now()
                # Reset OTP fields
                job.otp_code = ''
                job.otp_requested = False
                job.otp_sent = False
                job.save()
                
                # Catat OrderActivityLog
                tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
                OrderActivityLog.objects.create(
                    order=job.order_item.order,
                    user=user,
                    tindakan="FAIL_JOB",
                    keterangan=f"Pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}' gagal/dibatalkan (Status: {job.get_status_pekerjaan_display()})"
                )
                
            # 5. Kendala
            elif new_status == 'kendala':
                job.waktu_selesai = None
                job.save()
                
                # Catat OrderActivityLog
                tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
                OrderActivityLog.objects.create(
                    order=job.order_item.order,
                    user=user,
                    tindakan="CONSTRAINT_JOB",
                    keterangan=f"Pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}' mengalami kendala"
                )

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

        # Catat di OrderActivityLog
        tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
        OrderActivityLog.objects.create(
            order=job.order_item.order,
            user=user,
            tindakan="CLAIM_JOB",
            keterangan=f"Staff '{user.username}' mengambil/mengklaim item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}'"
        )
        
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
            
        # ✅ Enforce transition rule: only antrean/kendala/gagal -> dikerjakan
        if job.status_pekerjaan not in ('antrean', 'kendala', 'gagal'):
            return Response(
                {'error': f"Transisi status '{job.status_pekerjaan}' → 'dikerjakan' tidak diizinkan. Pekerjaan harus dalam Antrean, Kendala, atau Gagal."},
                status=status.HTTP_400_BAD_REQUEST
            )

        job.status_pekerjaan = 'dikerjakan'
        job.waktu_mulai = timezone.now()
        job.save()

        # Catat di OrderActivityLog
        tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
        OrderActivityLog.objects.create(
            order=job.order_item.order,
            user=user,
            tindakan="START_JOB",
            keterangan=f"Staff '{user.username}' mulai memproses pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}'"
        )
        
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
            
        # ✅ Enforce transition rule: only dikerjakan -> selesai
        if job.status_pekerjaan != 'dikerjakan':
            return Response(
                {'error': f"Transisi status '{job.status_pekerjaan}' → 'selesai' tidak diizinkan. Pekerjaan harus berstatus 'Sedang Dikerjakan' sebelum diselesaikan."},
                status=status.HTTP_400_BAD_REQUEST
            )

        job.status_pekerjaan = 'selesai'
        job.waktu_selesai = timezone.now()
        # Reset OTP fields
        job.otp_code = ''
        job.otp_requested = False
        job.otp_sent = False
        job.save()

        # Catat di OrderActivityLog
        tahap_nama = job.tahap.nama if job.tahap else "Tahap Awal"
        OrderActivityLog.objects.create(
            order=job.order_item.order,
            user=user,
            tindakan="COMPLETE_JOB",
            keterangan=f"Staff '{user.username}' berhasil menyelesaikan pengerjaan item '{job.order_item.jenis_produk}' pada tahap '{tahap_nama}'"
        )
        
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
            order._current_user = user
            order.save()
            
            OrderActivityLog.objects.create(
                order=order,
                user=user,
                tindakan="READY_ORDER",
                keterangan="Semua item selesai diproduksi. Status pesanan diubah otomatis menjadi 'Siap Diambil'."
            )
            
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
# CONTACT STATS VIEW — Standalone APIView untuk statistik pelanggan
# ---------------------------------------------------------
class ContactStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum, Count
        
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
        
        from .serializers import ContactSerializer
        return Response({
            'total_customers': total_customers,
            'total_revenue': total_revenue,
            'avg_order_value': int(avg_order_value),
            'total_piutang': total_piutang,
            'top_customers': ContactSerializer(top_customers, many=True).data,
        })


# ---------------------------------------------------------
# DASHBOARD VIEW — Agregasi data untuk halaman Dashboard
# ---------------------------------------------------------
class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cache_key = "dashboard_data"
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data)

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

        data = {
            'total_order_hari_ini': total_order_hari_ini,
            'total_order_bulan_ini': total_order_bulan_ini,
            'omset_bulan_ini': omset_bulan_ini,
            'omset_6_bulan': omset_6_bulan,
            'order_per_status': order_per_status,
            'job_per_status': job_per_status,
            'top_staff': top_staff,
            'stok_kritis': stok_kritis,
        }
        cache.set(cache_key, data, timeout=300)
        return Response(data)


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

        # Validasi tahap_id dan ambil tahap_baru SEBELUM transaction.atomic()
        tahap_baru = None
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
                # Bersihkan footer instruksi/konfirmasi
                detail_bersih = _re.split(r'(?i)===?\s*AKHIR\s*TEMPLATE\s*===?|⚠️\s*\*?PENTING:\*?|data\s+sudah\s+sesuai', message)[0].strip()
                try:
                    order_id = self._simpan_order_dari_form(sender, nama_pelanggan, detail_bersih)
                    label = "Konsep desain sudah masuk ke Antrean Desain" if is_form_desain else "Pesanan Anda telah masuk ke sistem kami"
                    jawaban = (
                        f"Terima kasih {panggilan}! {label} ✅\n\n"
                        f"🎫 *ID PESANAN: {order_id}*\n"
                        f"_Simpan ID ini untuk melacak status pesanan Kakak._\n\n"
                        f"Tim kami akan segera memverifikasi pesanan Kakak. Mohon ditunggu 🙏"
                    )
                except ValueError as ve:
                    jawaban = (
                        f"Maaf {panggilan}, format pengisian form pesanan Kakak belum lengkap / ada yang salah:\n\n"
                        f"⚠️ {str(ve)}\n\n"
                        f"Mohon perbaiki dan kirimkan ulang dengan format yang benar ya Kak. 🙏😊"
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
                # Pakai [^\r\n]* agar tidak match lintas baris pada sistem operasi / platform yang mengirim CRLF atau CR saja
                match = _re.search(
                    rf'-\s*{_re.escape(key)}\s*:\s*([^\r\n]*)',
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
                status_global='draft',
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
# 12b. WEBHOOK EVOLUTION API (BOT WHATSAPP)
# ---------------------------------------------------------
class EvolutionWebhookView(APIView):
    """
    Webhook endpoint untuk Evolution API. AllowAny.
    Memproses pesan masuk, mendeteksi anti-duplikasi, mengeksekusi logika wa_logic,
    dan mengirim balasan asinkron via REST API Client.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        from .wa_logic import (
            menunggu_nama,
            simpan_ke_memori, cek_tracking, cek_harga, cek_rules_awal,
            cek_database_faq, tanya_ai_finishing, ekstrak_nama_dari_pesan,
        )

        data = request.data

        # 1. Validasi API Key
        from django.utils.crypto import constant_time_compare
        auth_key = request.headers.get("apikey") or request.headers.get("Authorization", "")
        expected_key = os.getenv("EVOLUTION_API_KEY", "LocalTestingApiKey123")
        
        is_valid = False
        if auth_key and expected_key:
            if constant_time_compare(auth_key, expected_key):
                is_valid = True
            elif auth_key.startswith("Bearer ") and constant_time_compare(auth_key, f"Bearer {expected_key}"):
                is_valid = True

        if not is_valid:
            logger.warning(f"Unauthorized Webhook request dengan apikey: {auth_key}")
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        # Hanya proses event messages.upsert
        event_type = data.get('event')
        if event_type and event_type != "messages.upsert":
            return Response({'status': 'ignored_event_type', 'event': event_type}, status=status.HTTP_200_OK)

        event_data = data.get('data', {})
        if not event_data:
            return Response({'error': 'No data payload found'}, status=status.HTTP_400_BAD_REQUEST)

        key = event_data.get('key', {})
        from_me = key.get('fromMe', False)
        if from_me:
            return Response({'status': 'ignored_from_me'}, status=status.HTTP_200_OK)

        sender = key.get('remoteJid', '')
        sender_number = sender.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        if not sender_number:
            return Response({'error': 'No sender number found'}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Inbound Deduplication (Anti-Duplikasi Masuk)
        message_id = key.get('id', '')
        if message_id:
            inbound_cache_key = f"evo_inbound_{message_id}"
            if cache.get(inbound_cache_key):
                logger.info(f"Duplicate inbound message ID {message_id} diabaikan.")
                return Response({'status': 'duplicate_ignored'}, status=status.HTTP_200_OK)
            cache.set(inbound_cache_key, True, timeout=300) # 5 menit TTL

        # Ekstrak konten pesan
        msg_content = event_data.get('message', {})
        message_text = ""
        if isinstance(msg_content, dict):
            message_text = (
                msg_content.get('conversation', '') or
                msg_content.get('extendedTextMessage', {}).get('text', '') or
                msg_content.get('imageMessage', {}).get('caption', '') or
                msg_content.get('videoMessage', {}).get('caption', '') or
                msg_content.get('documentMessage', {}).get('caption', '') or
                ''
            ).strip()
        elif isinstance(msg_content, str):
            message_text = msg_content.strip()

        if not message_text:
            return Response({'status': 'ignored_empty_message'}, status=status.HTTP_200_OK)

        # Check if the sender is a staff member submitting attendance reason
        from api.models import CustomUser
        cleaned_sender = sender_number.lstrip('+').lstrip('0')
        if cleaned_sender.startswith('62'):
            cleaned_sender = cleaned_sender[2:]
            
        staff_user = None
        for u in CustomUser.objects.filter(is_active=True, role='staff'):
            if u.no_hp:
                u_wa = u.no_hp.replace('+', '').replace(' ', '').replace('-', '').lstrip('0')
                if u_wa.startswith('62'):
                    u_wa = u_wa[2:]
                if u_wa == cleaned_sender:
                    staff_user = u
                    break

        if staff_user:
            from hr.models import DailyAttendanceSession, UnlockRequest
            today = timezone.localdate()
            sesi = DailyAttendanceSession.objects.filter(tanggal=today).first()
            if sesi:
                unlock_req = UnlockRequest.objects.filter(staff=staff_user, sesi=sesi).order_by('-waktu_request').first()
                if unlock_req:
                    # Update their reason with this incoming message text!
                    unlock_req.alasan = message_text
                    unlock_req.save()
                    
                    # Send a confirmation WhatsApp message back to the staff
                    confirm_msg = (
                        f"Terima kasih {staff_user.get_full_name() or staff_user.username}.\n\n"
                        f"Alasan Anda:\n"
                        f"*\"{message_text}\"*\n\n"
                        f"Telah berhasil dicatat dan diteruskan ke Manager untuk ditinjau. "
                        f"Anda akan menerima notifikasi jika akses Anda disetujui."
                    )
                    self._kirim_balas_async(sender_number, confirm_msg)
                    
                    # Also notify the Manager / Owner!
                    try:
                        manager_user = sesi.dihidupkan_oleh or CustomUser.objects.filter(role__in=['manager', 'owner'], is_active=True).first()
                        if manager_user and manager_user.no_hp:
                            mgr_wa = manager_user.no_hp.replace('+', '').replace(' ', '').replace('-', '')
                            mgr_msg = (
                                f"🚨 *PEMBERITAHUAN ABSENSI STAFF* 🚨\n\n"
                                f"Staff *{staff_user.get_full_name() or staff_user.username}* memberikan alasan absensi masuk hari ini:\n"
                                f"💬 *\"{message_text}\"*\n\n"
                                f"Silakan periksa halaman dashboard HR CRM untuk menyetujui (Approve) atau menolak (Reject) permintaan buka kunci."
                            )
                            self._kirim_balas_async(mgr_wa, mgr_msg)
                    except Exception as e:
                        logger.error(f"Gagal mengirim notifikasi alasan staff ke manager: {e}")
                        
                    return Response({'status': 'staff_attendance_reason_captured'}, status=status.HTTP_200_OK)

        # Ambil kontak
        contact_obj = Contact.objects.filter(nomor_wa=sender_number).first()
        nama_pelanggan = contact_obj.nama if contact_obj else ""
        p_kecil = message_text.lower()
        panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

        # Cek status Human Handover
        if contact_obj and getattr(contact_obj, 'handover_to_staff', False):
            logger.info(f"Chat dengan {sender_number} sedang dalam mode Human Handover. Bot diabaikan.")
            return Response({'status': 'handover_mode_active'}, status=status.HTTP_200_OK)

        jawaban = ""

        # Step 1: Tanya nama jika kontak baru
        if not nama_pelanggan and sender_number not in menunggu_nama:
            menunggu_nama.add(sender_number)
            try:
                biz_name = SystemConfig.objects.get(key='bisnis_nama').value or 'Brandy'
            except Exception:
                biz_name = 'Brandy'
            jawaban = (
                f"Halo Kak! 👋 Selamat datang di *{biz_name}*.\n"
                "Boleh tahu dengan Kakak siapa ini biar lebih enak ngobrolnya? 😊"
            )
            self._kirim_balas_async(sender_number, jawaban)
            return Response({'status': 'waiting_for_name_triggered'}, status=status.HTTP_200_OK)

        elif sender_number in menunggu_nama:
            nama_baru = ekstrak_nama_dari_pesan(message_text)
            contact_obj, _ = Contact.objects.get_or_create(
                nomor_wa=sender_number, defaults={'nama': nama_baru}
            )
            if not contact_obj.nama:
                contact_obj.nama = nama_baru
                contact_obj.save()
            elif contact_obj.nama != nama_baru:
                contact_obj.nama = nama_baru
                contact_obj.save()
            menunggu_nama.discard(sender_number)
            nama_pelanggan = nama_baru
            panggilan = f"Kak {nama_pelanggan}"
            jawaban = (
                f"Salam kenal {panggilan}! ✨\n"
                f"Ada yang bisa kami bantu hari ini? Mau cetak apa nih Kak?"
            )
            self._kirim_balas_async(sender_number, jawaban)
            return Response({'status': 'name_registered'}, status=status.HTTP_200_OK)

        # Simpan pesan masuk ke memori AI
        simpan_ke_memori(sender_number, "user", message_text, nama_pelanggan)

        # Step 2: Cek tracking pesanan
        jawaban = cek_tracking(message_text, sender_number, nama_pelanggan)
        if jawaban:
            simpan_ke_memori(sender_number, "assistant", jawaban, nama_pelanggan)
            self._kirim_balas_async(sender_number, jawaban)
            return Response({'status': 'tracking_replied'}, status=status.HTTP_200_OK)

        # Step 3: Deteksi form order / desain
        is_form_order = (
            ('jenis produk' in p_kecil and ('no. wa' in p_kecil or 'item 1' in p_kecil or 'no wa' in p_kecil))
            or
            ('nama pemesan' in p_kecil and 'jenis produk' in p_kecil)
        )
        is_form_desain = 'tulisan yang dimuat' in p_kecil or 'dominan warna' in p_kecil

        if is_form_order or is_form_desain:
            # Bersihkan footer instruksi/konfirmasi
            detail_bersih = _re.split(r'(?i)===?\s*AKHIR\s*TEMPLATE\s*===?|⚠️\s*\*?PENTING:\*?|data\s+sudah\s+sesuai', message_text)[0].strip()
            try:
                order_id = self._simpan_order_dari_form(sender_number, nama_pelanggan, detail_bersih)
                label = "Konsep desain sudah masuk ke Antrean Desain" if is_form_desain else "Pesanan Anda telah masuk ke sistem kami"
                jawaban = (
                    f"Terima kasih {panggilan}! {label} ✅\n\n"
                    f"🎫 *ID PESANAN: {order_id}*\n"
                    f"_Simpan ID ini untuk melacak status pesanan Kakak._\n\n"
                    f"Tim kami akan segera memverifikasi pesanan Kakak. Mohon ditunggu 🙏"
                )
            except ValueError as ve:
                jawaban = (
                    f"Maaf {panggilan}, format pengisian form pesanan Kakak belum lengkap / ada yang salah:\n\n"
                    f"⚠️ {str(ve)}\n\n"
                    f"Mohon perbaiki dan kirimkan ulang dengan format yang benar ya Kak. 🙏😊"
                )

        # Step 4: Cek tanya harga
        if not jawaban:
            jawaban = cek_harga(message_text, nama_pelanggan)

        # Step 5: Cek rules awal
        if not jawaban:
            jawaban = cek_rules_awal(message_text, sender_number, nama_pelanggan)

        # Step 6: FAQ dari database
        if not jawaban:
            jawaban = cek_database_faq(message_text, nama_pelanggan)

        # Step 7: AI Fallback
        if not jawaban:
            jawaban = tanya_ai_finishing(sender_number)

        simpan_ke_memori(sender_number, "assistant", jawaban, nama_pelanggan)
        self._kirim_balas_async(sender_number, jawaban)

        return Response({'status': 'processed'}, status=status.HTTP_200_OK)

    def _kirim_balas_async(self, number, text):
        """
        Kirim balasan dengan simulasi mengetik secara asinkron di thread terpisah.
        Tracks task status in Django cache.
        """
        import threading
        import time
        import uuid
        from django.core.cache import cache

        task_id = f"async_task_{uuid.uuid4().hex}"
        cache.set(task_id, {"status": "pending", "number": number, "text": text[:50], "timestamp": time.time()}, timeout=3600)

        def worker():
            cache.set(task_id, {"status": "running", "number": number, "text": text[:50], "timestamp": time.time()}, timeout=3600)
            
            # Hitung delay berdasarkan panjang karakter (misal 30 karakter per detik)
            char_delay = len(text) / 30.0
            total_delay = min(max(2.0, char_delay), 15.0)

            # Tampilkan status sedang mengetik
            try:
                whatsapp_client.send_presence(number, "composing")
            except Exception as e:
                logger.warning(f"Failed to send presence composing: {e}")
                
            time.sleep(total_delay)
            
            # Kirim pesan dan matikan presence dengan retry
            success = False
            error_msg = ""
            for attempt in range(3):
                try:
                    res = whatsapp_client.send_text_message(number, text)
                    if res:
                        success = True
                        break
                    else:
                        error_msg = "Evolution API returned empty response"
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Attempt {attempt+1} failed to send WA message: {e}")
                time.sleep(2)
            
            try:
                whatsapp_client.send_presence(number, "paused")
            except Exception as e:
                logger.warning(f"Failed to send presence paused: {e}")

            if success:
                cache.set(task_id, {"status": "success", "number": number, "timestamp": time.time()}, timeout=3600)
            else:
                logger.critical(f"[WA_SEND_FAILURE] Failed to send message to {number} after 3 attempts. Error: {error_msg}. Text: {text}")
                cache.set(task_id, {"status": "failed", "number": number, "error": error_msg, "timestamp": time.time()}, timeout=86400)

        threading.Thread(target=worker, daemon=True).start()
        return task_id

    def _simpan_order_dari_form(self, nomor, nama_kontak, detail):
        def ambil_field(teks, *keys):
            for key in keys:
                # Pakai [^\r\n]* agar tidak match lintas baris pada platform dengan CR atau CRLF
                match = _re.search(
                    rf'(?:[-*••]|\d+\.)?\s*{_re.escape(key)}\s*[:=]\s*([^\r\n]*)',
                    teks, _re.IGNORECASE
                )
                if match:
                    val = match.group(1).strip().strip('*_')
                    if val and val not in ('-', 'sudah ada / belum ada', '*sudah ada* / *belum ada*'):
                        return val
            return ''

        nama_dari_form = (
            ambil_field(detail, 'Nama Pemesan', 'Nama') or nama_kontak or '-'
        )

        blok_items = _re.split(r'(?im)^\s*[-*•\[\*_]*\s*(?:📦\s*)?[\*_]*item\s+\d+[\*_\]:]*\s*[\*_]*.*$', detail)
        blok_items = [b.strip() for b in blok_items if b.strip()]

        if len(blok_items) <= 1:
            blok_items = [detail]

        nama_order = nama_dari_form

        with transaction.atomic():
            contact, _ = Contact.objects.get_or_create(
                nomor_wa=nomor, defaults={'nama': nama_kontak}
            )
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
                nomor_wa=contact.nomor_wa,
                nama=nama_order,
                status_global='draft',
                catatan_pelanggan='',
            )

            items_dibuat = 0
            items_parsed_info = []
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

                if jenis_produk == 'Umum' and not ukuran and not bahan:
                    continue

                if not jumlah_str or not jumlah_str.strip():
                    raise ValueError(f"Kolom 'Jumlah' pada Item {items_dibuat+1} tidak boleh kosong.")
                
                digits_only = ''.join(filter(str.isdigit, jumlah_str))
                if not digits_only:
                    raise ValueError(f"Jumlah '{jumlah_str}' pada Item {items_dibuat+1} tidak mengandung angka yang valid.")
                
                try:
                    qty = int(digits_only)
                    if qty <= 0:
                        raise ValueError(f"Jumlah '{qty}' pada Item {items_dibuat+1} harus lebih dari nol.")
                except ValueError:
                    raise ValueError(f"Jumlah '{jumlah_str}' pada Item {items_dibuat+1} bukan angka yang valid.")

                # Parse panjang & lebar
                panjang = 0.0
                lebar = 0.0
                if ukuran:
                    dimensi_match = _re.search(r'([\d.,]+)\s*[xX*]\s*([\d.,]+)', ukuran)
                    if dimensi_match:
                        try:
                            panjang = float(dimensi_match.group(1).replace(',', '.'))
                            lebar = float(dimensi_match.group(2).replace(',', '.'))
                        except ValueError:
                            pass

                detail_json = []
                if ukuran: detail_json.append({"key": "Ukuran", "value": ukuran})
                if finishing: detail_json.append({"key": "Finishing", "value": finishing})
                if bahan: detail_json.append({"key": "Bahan", "value": bahan})

                gdrive_link = ''
                link_match = _re.search(r'(https?://\S+)', blok)
                if link_match:
                    gdrive_link = link_match.group(1)

                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk=jenis_produk,
                    qty=qty,
                    panjang=panjang,
                    lebar=lebar,
                    bahan=bahan or '',
                    harga_jual=0,
                    detail=detail_json,
                    keterangan_detail=keterangan or '',
                    gdrive_customer_link=gdrive_link,
                )

                items_parsed_info.append({
                    'qty': qty,
                    'jenis_produk': jenis_produk,
                    'keterangan': keterangan
                })

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

            if items_dibuat == 0:
                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk='Umum',
                    qty=1,
                    harga_jual=0,
                    detail=[{"key": "Info", "value": "Format tidak terurai"}],
                    keterangan_detail=detail[:200],
                )
                tahap_awal = TahapProses.objects.order_by('urutan').first()
                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )
                order.catatan_pelanggan = "Pemesanan dari WhatsApp (Format tidak terurai)"[:500]
                order.save()
            else:
                summary_parts = []
                for item in items_parsed_info:
                    part = f"{item['qty']}x {item['jenis_produk']}"
                    if item['keterangan']:
                        part += f" ({item['keterangan']})"
                    summary_parts.append(part)
                order.catatan_pelanggan = ", ".join(summary_parts)[:500]
                order.save()

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
            jobs = list(staff.my_tasks.all())
            
            # Saring berdasarkan rentang waktu jika diset
            if start and end:
                completed_jobs = [j for j in jobs if j.status_pekerjaan == 'selesai' and j.waktu_selesai and start <= j.waktu_selesai <= end]
                failed_jobs = [j for j in jobs if j.status_pekerjaan in ['gagal', 'batal'] and j.waktu_selesai and start <= j.waktu_selesai <= end]
            else:
                completed_jobs = [j for j in jobs if j.status_pekerjaan == 'selesai']
                failed_jobs = [j for j in jobs if j.status_pekerjaan in ['gagal', 'batal']]
                
            active_jobs = [j for j in jobs if j.status_pekerjaan == 'dikerjakan']
            pending_jobs = [j for j in jobs if j.status_pekerjaan == 'antrean']
            constraint_jobs = [j for j in jobs if j.status_pekerjaan == 'kendala']

            total_jobs = len(completed_jobs) + len(failed_jobs) + len(active_jobs) + len(pending_jobs) + len(constraint_jobs)
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
                'jobs_completed': len(completed_jobs),
                'jobs_in_progress': len(active_jobs),
                'jobs_pending': len(pending_jobs),
                'jobs_failed': len(failed_jobs),
                'jobs_constraint': len(constraint_jobs),
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


class HealthCheckView(APIView):
    """GET /api/health/ — Cek status semua komponen sistem."""
    permission_classes = [AllowAny]

    def get(self, request):
        from django.db import connection
        
        status_db = False
        try:
            connection.ensure_connection()
            status_db = True
        except Exception:
            pass

        status_cache = False
        try:
            cache.set('health_check', 'ok', 5)
            status_cache = cache.get('health_check') == 'ok'
        except Exception:
            pass

        overall = status_db and status_cache
        return Response({
            'status': 'ok' if overall else 'degraded',
            'database': 'ok' if status_db else 'error',
            'cache': 'ok' if status_cache else 'error',
            'timestamp': timezone.now().isoformat(),
        }, status=200 if overall else 503)


# ---------------------------------------------------------
# KOMPLAIN VIEWSET — CRUD + Resolve Action
# ---------------------------------------------------------
class KomplainViewSet(viewsets.ModelViewSet):
    """
    GET    /api/komplain/            — Semua komplain (owner/manager: semua; staff/admin: hanya yang dicatat sendiri)
    POST   /api/komplain/            — Buat komplain baru (semua role)
    PATCH  /api/komplain/{id}/       — Update status/resolusi (owner/manager)
    POST   /api/komplain/{id}/resolve/ — Tutup komplain dengan resolusi (owner/manager)
    """
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


# ---------------------------------------------------------
# CRM: CUSTOMER ACTIVITY VIEWSET
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# MRP: BILL OF MATERIALS (BOM) & ITEM VIEWSETS
# ---------------------------------------------------------
class BillOfMaterialsViewSet(viewsets.ModelViewSet):
    queryset = BillOfMaterials.objects.select_related('product').prefetch_related('items__inventory_item').all()
    serializer_class = BillOfMaterialsSerializer
    permission_classes = [IsOwnerOrManager]


class BoMItemViewSet(viewsets.ModelViewSet):
    queryset = BoMItem.objects.select_related('bom', 'inventory_item').all()
    serializer_class = BoMItemSerializer
    permission_classes = [IsOwnerOrManager]


# ---------------------------------------------------------
# WHATSAPP INTEGRATION: STATUS, QR CODE, CHATS, MESSAGES, SEND
# ---------------------------------------------------------
class WhatsAppStatusView(APIView):
    """
    GET /api/whatsapp/status/
    Returns the current connection state of the WhatsApp instance
    and a QR code (base64) for scanning if not yet connected.
    Owner/Manager only.
    """
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        import requests as req_lib
        base_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080").rstrip('/')
        api_key  = os.getenv("EVOLUTION_API_KEY", "LocalTestingApiKey123")
        instance = os.getenv("EVOLUTION_INSTANCE_NAME", "bintang_instance")
        headers  = {"apikey": api_key}

        # 1. Get connection state
        state = "unknown"
        owner_jid = None
        try:
            r = req_lib.get(f"{base_url}/instance/connectionState/{instance}", headers=headers, timeout=5)
            if r.ok:
                data = r.json()
                state = data.get("instance", {}).get("state", "unknown")
        except Exception as e:
            logger.warning(f"Could not fetch WA connection state: {e}")

        # 2. If not connected, get QR code
        qr_base64 = None
        pairing_code = None
        if state in ("connecting", "close", "unknown"):
            try:
                r = req_lib.get(f"{base_url}/instance/connect/{instance}", headers=headers, timeout=10)
                if r.ok:
                    data = r.json()
                    qr_base64 = data.get("base64")
                    pairing_code = data.get("pairingCode")
            except Exception as e:
                logger.warning(f"Could not fetch WA QR code: {e}")

        # 3. Get instance info (message/chat count etc)
        instance_info = {}
        try:
            r = req_lib.get(f"{base_url}/instance/fetchInstances", headers=headers, timeout=5)
            if r.ok:
                instances = r.json()
                for inst in (instances if isinstance(instances, list) else []):
                    if inst.get("name") == instance:
                        instance_info = {
                            "ownerJid":    inst.get("ownerJid"),
                            "profileName": inst.get("profileName"),
                            "messageCount": inst.get("_count", {}).get("Message", 0),
                            "chatCount":    inst.get("_count", {}).get("Chat", 0),
                        }
                        owner_jid = inst.get("ownerJid")
                        break
        except Exception as e:
            logger.warning(f"Could not fetch WA instance info: {e}")

        return Response({
            "state":        state,
            "connected":    state == "open",
            "owner_jid":    owner_jid,
            "qr_base64":    qr_base64,
            "pairing_code": pairing_code,
            "instance_name": instance,
            **instance_info,
        })


class WhatsAppChatsView(APIView):
    """
    GET /api/whatsapp/chats/
    Retrieves all active chats from the WhatsApp Gateway (Evolution API).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        chats = whatsapp_client.get_chats()
        return Response(chats)


class WhatsAppMessagesView(APIView):
    """
    GET /api/whatsapp/messages/?number=628xx
    Retrieves message history for a specific number from Evolution API.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        number = request.query_params.get('number')
        if not number:
            return Response({"error": "Query parameter 'number' is required"}, status=400)
        
        limit = int(request.query_params.get('limit', 50))
        messages = whatsapp_client.get_messages(number, limit=limit)
        return Response(messages)


class WhatsAppSendMessageView(APIView):
    """
    POST /api/whatsapp/send/
    Sends a WhatsApp message manually to a contact.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        number = request.data.get('number')
        text = request.data.get('text')
        if not number or not text:
            return Response({"error": "Fields 'number' and 'text' are required"}, status=400)
        
        result = whatsapp_client.send_text_message(number, text)
        if result:
            return Response(result)
        return Response({"error": "Failed to send message via WhatsApp Gateway"}, status=500)


class WhatsAppSendMediaView(APIView):
    """
    POST /api/whatsapp/send-media/
    Uploads a file to Cloudflare R2 / Django storage, then sends it via WhatsApp.
    Accepts:
      - file: multipart file
      - number: string
      - caption: string (optional)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import mimetypes
        from django.core.files.storage import default_storage
        from django.utils.text import get_valid_filename

        number = request.data.get('number')
        caption = request.data.get('caption', '')
        file_obj = request.FILES.get('file')

        if not number or not file_obj:
            return Response({"error": "Fields 'number' and 'file' are required"}, status=400)

        # 1. Clean file name
        cleaned_filename = get_valid_filename(file_obj.name)
        
        # Save to storage (R2 in production, local in dev)
        unique_name = f"whatsapp_media/{uuid.uuid4().hex}_{cleaned_filename}"
        
        try:
            saved_path = default_storage.save(unique_name, file_obj)
            relative_url = default_storage.url(saved_path)
            
            # Make sure we have an absolute URL
            if relative_url.startswith('/'):
                media_url = request.build_absolute_uri(relative_url)
            else:
                media_url = relative_url

            # 2. Detect mime type & media type
            mime_type, _ = mimetypes.guess_type(cleaned_filename)
            if not mime_type:
                mime_type = "application/octet-stream"

            media_type = "document"
            if mime_type.startswith("image/"):
                media_type = "image"
            elif mime_type.startswith("video/"):
                media_type = "video"
            elif mime_type.startswith("audio/"):
                media_type = "audio"

            # 3. Send via Evolution API
            result = whatsapp_client.send_media_message(
                number=number,
                media_url=media_url,
                media_type=media_type,
                mime_type=mime_type,
                file_name=cleaned_filename,
                caption=caption
            )

            if result:
                return Response({
                    "status": "success",
                    "media_url": media_url,
                    "result": result
                })
            
            return Response({"error": "Failed to send media via WhatsApp Gateway"}, status=500)

        except Exception as e:
            logger.error(f"Error handling WhatsApp send media upload: {e}", exc_info=True)
            return Response({"error": str(e)}, status=500)


class ClientLogView(APIView):
    """
    POST /api/log-client-error/
    Logs frontend/client-side uncaught crashes to backend log files.
    """
    permission_classes = [] # Allow anonymous logging if crash occurs before login config is fully resolved

    def post(self, request):
        error_msg = request.data.get('error', 'Unknown Error')
        error_info = request.data.get('info', {})
        url = request.data.get('url', 'Unknown URL')
        user_agent = request.META.get('HTTP_USER_AGENT', 'Unknown User Agent')
        
        logger.critical(
            f"[CLIENT_CRASH] Uncaught frontend exception:\n"
            f"URL: {url}\n"
            f"Error: {error_msg}\n"
            f"Component Stack: {error_info.get('componentStack', 'N/A')}\n"
            f"User Agent: {user_agent}"
        )
        return Response({"status": "error_logged"}, status=200)