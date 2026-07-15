import random
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from decimal import Decimal
from .pos_models import POSSale, POSSaleItem
from .pos_serializers import POSSaleSerializer
from .models import SaldoKasHarian, Contact
from .product_models import Product, ProductVariant, ProductStockMovement

class POSSaleViewSet(viewsets.ModelViewSet):
    queryset = POSSale.objects.all().order_by('-created_at')
    serializer_class = POSSaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Allow filtering by shift or kasir or status
        qs = super().get_queryset()
        shift_id = self.request.query_params.get('shift')
        kasir_id = self.request.query_params.get('kasir')
        status_val = self.request.query_params.get('status')
        
        if shift_id:
            qs = qs.filter(shift_id=shift_id)
        if kasir_id:
            qs = qs.filter(kasir_id=kasir_id)
        if status_val:
            qs = qs.filter(status=status_val)
        return qs

    def create(self, request, *args, **kwargs):
        items = request.data.get('items', [])
        if not items:
            return Response({'error': 'Tidak ada item dalam keranjang.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                # Auto generate nomor transaksi
                now = timezone.now()
                nomor = f"POS-{now.strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
                
                # Cari shift yang sedang aktif (kas_akhir null)
                shift = SaldoKasHarian.objects.filter(kas_akhir__isnull=True).last()
                
                # Buat header penjualan
                subtotal = request.data.get('subtotal', 0)
                diskon = request.data.get('diskon', 0)
                pajak = request.data.get('pajak', 0)
                total = request.data.get('total', 0)
                dibayar = request.data.get('dibayar', 0)
                kembalian = request.data.get('kembalian', 0)
                metode_bayar = request.data.get('metode_bayar', 'Cash')
                catatan = request.data.get('catatan', '')
                status_val = request.data.get('status', 'paid')
                pelanggan_id = request.data.get('pelanggan')

                sale = POSSale.objects.create(
                    nomor=nomor,
                    kasir=request.user,
                    pelanggan_id=pelanggan_id,
                    shift=shift,
                    subtotal=subtotal,
                    diskon=diskon,
                    pajak=pajak,
                    total=total,
                    metode_bayar=metode_bayar,
                    dibayar=dibayar,
                    kembalian=kembalian,
                    catatan=catatan,
                    status=status_val
                )

                for item in items:
                    product_id = item.get('product_id')
                    variant_id = item.get('variant_id')
                    qty = float(item.get('qty', 1))
                    
                    # Lock row product
                    product = Product.objects.select_for_update().get(id=product_id)
                    variant = ProductVariant.objects.select_for_update().get(id=variant_id) if variant_id else None
                    
                    nama_snapshot = item.get('nama', product.nama)
                    harga_snapshot = float(item.get('harga', 0))
                    subtotal_item = harga_snapshot * qty
                    
                    POSSaleItem.objects.create(
                        sale=sale,
                        product=product,
                        variant=variant,
                        nama_snapshot=nama_snapshot,
                        harga_snapshot=harga_snapshot,
                        qty=qty,
                        subtotal=subtotal_item,
                        catatan=item.get('catatan', '')
                    )
                    
                    # Potong stok jika status paid/lunas
                    if status_val == 'paid':
                        if variant:
                            if product.lacak_inventori:
                                stok_awal = variant.qty_stok
                                variant.qty_stok -= Decimal(str(qty))
                                variant.save()
                                product.qty_stok -= Decimal(str(qty))
                                product.save()
                                
                                ProductStockMovement.objects.create(
                                    product=product,
                                    variant=variant,
                                    user=request.user,
                                    tipe='penjualan',
                                    qty=Decimal(str(qty)),
                                    stok_awal=stok_awal,
                                    stok_akhir=variant.qty_stok,
                                    catatan=f"Penjualan POS {sale.nomor}",
                                    tanggal=now.date(),
                                )
                        else:
                            if product.lacak_inventori:
                                stok_awal = product.qty_stok
                                product.qty_stok -= Decimal(str(qty))
                                product.save()
                                
                                ProductStockMovement.objects.create(
                                    product=product,
                                    user=request.user,
                                    tipe='penjualan',
                                    qty=Decimal(str(qty)),
                                    stok_awal=stok_awal,
                                    stok_akhir=product.qty_stok,
                                    catatan=f"Penjualan POS {sale.nomor}",
                                    tanggal=now.date(),
                                )
                
                serializer = self.get_serializer(sale)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    from rest_framework.decorators import action

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        sale = self.get_object()
        if sale.status == 'void':
            return Response({'error': 'Transaksi sudah dibatalkan sebelumnya.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                # Kembalikan stok jika sebelumnya status paid/lunas
                if sale.status == 'paid':
                    for item in sale.items.all():
                        if item.product:
                            product = Product.objects.select_for_update().get(id=item.product.id)
                            variant = ProductVariant.objects.select_for_update().get(id=item.variant.id) if item.variant else None
                            qty = float(item.qty)

                            if variant:
                                if product.lacak_inventori:
                                    stok_awal = variant.qty_stok
                                    variant.qty_stok += Decimal(str(qty))
                                    variant.save()
                                    product.qty_stok += Decimal(str(qty))
                                    product.save()
                                    
                                    ProductStockMovement.objects.create(
                                        product=product,
                                        variant=variant,
                                        user=request.user,
                                        tipe='pengembalian',
                                        qty=Decimal(str(qty)),
                                        stok_awal=stok_awal,
                                        stok_akhir=variant.qty_stok,
                                        catatan=f"Pembatalan POS (Void) {sale.nomor}",
                                        tanggal=timezone.now().date(),
                                    )
                            else:
                                if product.lacak_inventori:
                                    stok_awal = product.qty_stok
                                    product.qty_stok += Decimal(str(qty))
                                    product.save()
                                    
                                    ProductStockMovement.objects.create(
                                        product=product,
                                        user=request.user,
                                        tipe='pengembalian',
                                        qty=Decimal(str(qty)),
                                        stok_awal=stok_awal,
                                        stok_akhir=product.qty_stok,
                                        catatan=f"Pembatalan POS (Void) {sale.nomor}",
                                        tanggal=timezone.now().date(),
                                    )

                sale.status = 'void'
                sale.save()
                
                serializer = self.get_serializer(sale)
                return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
