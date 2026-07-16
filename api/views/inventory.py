import uuid
import logging
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction

from ..models import InventoryItem, RestockHistory
from ..serializers import InventoryItemSerializer
from ..permissions import IsOwnerManagerAdminOrReadOnly, IsOwnerManagerOrAdmin
from hr.models import Akun, TransaksiBukuBesar

logger = logging.getLogger(__name__)


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


class InventoryItemViewSet(viewsets.ModelViewSet):
    serializer_class   = InventoryItemSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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


class InventoryRestockView(APIView):
    """POST /api/inventory/<pk>/restock/ — Tambah/kurangi stok dan catat history."""
    permission_classes = [IsOwnerManagerOrAdmin]

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
