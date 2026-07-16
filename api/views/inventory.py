import uuid
import logging
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction

import os
import json
from rest_framework.decorators import action

from ..models import InventoryItem, RestockHistory, ProductPrice, BillOfMaterials, BoMItem
from ..serializers import (
    InventoryItemSerializer, ProductPriceSerializer, BillOfMaterialsSerializer, BoMItemSerializer
)
from ..permissions import IsOwnerManagerAdminOrReadOnly, IsOwnerManagerOrAdmin, IsOwnerOrManager
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


class ProductPriceViewSet(viewsets.ModelViewSet):
    queryset = ProductPrice.objects.all()
    serializer_class = ProductPriceSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    @action(detail=False, methods=['post'], url_path='seed')
    def seed_prices(self, request):
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


class BillOfMaterialsViewSet(viewsets.ModelViewSet):
    queryset = BillOfMaterials.objects.select_related('product').prefetch_related('items__inventory_item').all()
    serializer_class = BillOfMaterialsSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        queryset = self.queryset
        product_name = self.request.query_params.get('product_name')
        if product_name:
            queryset = queryset.filter(product__nama_produk=product_name)
        material = self.request.query_params.get('material')
        if material is not None:
            if material == '' or material.lower() == 'null':
                queryset = queryset.filter(product__material__isnull=True) | queryset.filter(product__material='')
            else:
                queryset = queryset.filter(product__material=material)
        return queryset

    @action(detail=False, methods=['post'], url_path='get-or-create-for-product')
    def get_or_create_for_product(self, request):
        product_name = request.data.get('product_name')
        if not product_name:
            return Response({'error': 'product_name wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        product_name = product_name.strip()
        
        material = request.data.get('material')
        if material:
            material = material.strip()
            if material == '0' or material.lower() == 'null':
                material = None
        else:
            material = None
            
        with transaction.atomic():
            # Find or create ProductPrice
            product_price_obj = ProductPrice.objects.filter(nama_produk=product_name, material=material).first()
            if not product_price_obj:
                if not material:
                    product_price_obj = ProductPrice.objects.filter(nama_produk=product_name).first()
                if not product_price_obj:
                    product_price_obj = ProductPrice.objects.create(
                        kategori="Umum",
                        nama_produk=product_name,
                        material=material,
                        harga=0
                    )
            
            # Find or create BillOfMaterials
            bom_obj, created = BillOfMaterials.objects.get_or_create(
                product=product_price_obj,
                defaults={'nama': f"BoM {product_price_obj.nama_produk}" + (f" - {product_price_obj.material}" if product_price_obj.material else "")}
            )
            
        serializer = self.get_serializer(bom_obj)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BoMItemViewSet(viewsets.ModelViewSet):
    queryset = BoMItem.objects.select_related('bom', 'inventory_item').all()
    serializer_class = BoMItemSerializer
    permission_classes = [IsOwnerOrManager]

