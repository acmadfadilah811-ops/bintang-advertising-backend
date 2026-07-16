import logging
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from ..models import (
    JobBoard, CustomUser, TahapProses, OrderActivityLog, InventoryItem, RestockHistory, ProductPrice, BillOfMaterials, BoMItem
)
from ..serializers import JobBoardSerializer
from ..permissions import IsClockedIn

# Imported from legacy for now; will be moved to inventory.py later
from api.views.legacy import record_material_consumption_to_general_ledger

logger = logging.getLogger(__name__)


def deduct_job_materials_if_needed(job, user):
    """
    Mengurangi stok bahan baku. Prioritas pertama menggunakan sistem Bill of Materials (BoM).
    Jika BoM tidak ditemukan untuk produk/bahan terkait, fallback ke input manual di catatan_staff.
    Juga mencatat konsumsi bahan ke Buku Besar (HPP).
    """
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
