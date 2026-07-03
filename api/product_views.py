import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .product_models import (
    ProductCategory, Brand, SpecialType, Collection,
    Product, ProductVariant, ProductPackage, ProductPackageItem, Addon, Specification,
    ProductStockMovement, ProductImage, StockInDocument, StockInDocumentItem,
    StockOutDocument, StockOutDocumentItem, StockProductionDocument, StockProductionDocumentItem,
    StockOpnameDocument, StockOpnameDocumentItem
)
from .product_serializers import (
    ProductCategorySerializer, BrandSerializer, SpecialTypeSerializer,
    CollectionSerializer, ProductSerializer, ProductVariantSerializer,
    ProductPackageSerializer, AddonSerializer, SpecificationSerializer,
    ProductStockMovementSerializer, ProductImageSerializer,
    StockInDocumentSerializer, StockInDocumentItemSerializer,
    StockOutDocumentSerializer, StockOutDocumentItemSerializer,
    StockProductionDocumentSerializer, StockProductionDocumentItemSerializer,
    StockOpnameDocumentSerializer, StockOpnameDocumentItemSerializer
)


def _to_decimal(raw, field_name):
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field_name} harus berupa angka")
    return value


def _csv_cell(row_lower, *keys):
    """Ambil nilai dari dict CSV row (key sudah di-lowercase), coba beberapa alias key."""
    for key in keys:
        if key in row_lower and row_lower[key]:
            return row_lower[key].strip()
    return ''


def _csv_row_lower(row):
    return {(k or '').strip().lower(): (v or '') for k, v in row.items()}


def _next_document_number(model, prefix):
    """Nomor dokumen berikutnya untuk prefix hari ini.
    Ambil nomor TERTINGGI (bukan count) supaya tidak bentrok setelah dokumen dihapus."""
    last = model.objects.filter(nomor__startswith=prefix).order_by('-nomor').first()
    if last:
        try:
            next_num = int(last.nomor[len(prefix):]) + 1
        except ValueError:
            next_num = 1
    else:
        next_num = 1
    return f"{prefix}{next_num:08d}"


def _parse_decimal_safe(raw, default=None):
    if default is None:
        default = Decimal('0')
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _parse_int_safe(raw, default=0):
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def _parse_bool_flag(raw):
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'ya')


def _parse_date_ddmmyyyy(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None

class ProductCategoryViewSet(viewsets.ModelViewSet):
    queryset = ProductCategory.objects.all().order_by('urutan')
    serializer_class = ProductCategorySerializer
    permission_classes = [IsAuthenticated]

class BrandViewSet(viewsets.ModelViewSet):
    queryset = Brand.objects.all().order_by('nama')
    serializer_class = BrandSerializer
    permission_classes = [IsAuthenticated]

class SpecialTypeViewSet(viewsets.ModelViewSet):
    queryset = SpecialType.objects.all().order_by('urutan')
    serializer_class = SpecialTypeSerializer
    permission_classes = [IsAuthenticated]

class CollectionViewSet(viewsets.ModelViewSet):
    queryset = Collection.objects.all().order_by('nama')
    serializer_class = CollectionSerializer
    permission_classes = [IsAuthenticated]

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().order_by('-created_at')
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get('kategori', None)
        if category is not None:
            queryset = queryset.filter(kategori__id=category)
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(nama__icontains=search)
        return queryset

    def _resolve_stock_owner(self, product, variant_id):
        """Kembalikan (objek yang disimpan qty_stok-nya, instance variant atau None)."""
        if variant_id:
            variant = ProductVariant.objects.select_for_update().get(pk=variant_id, product=product)
            return variant, variant
        return product, None

    def _apply_stock_movement(self, request, pk, tipe):
        qty_raw = request.data.get('qty')
        if qty_raw is None:
            return Response({'error': 'qty wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            qty = _to_decimal(qty_raw, 'qty')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if qty <= 0:
            return Response({'error': 'qty harus lebih besar dari 0'}, status=status.HTTP_400_BAD_REQUEST)

        harga_beli_raw = request.data.get('harga_beli')
        harga_beli = None
        if harga_beli_raw not in (None, ''):
            try:
                harga_beli = _to_decimal(harga_beli_raw, 'harga_beli')
            except ValueError as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        catatan = request.data.get('catatan', '')
        tanggal = request.data.get('tanggal')
        variant_id = request.data.get('variant')

        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=pk)
            owner, variant = self._resolve_stock_owner(product, variant_id)

            stok_awal = owner.qty_stok
            if tipe == 'masuk':
                stok_akhir = stok_awal + qty
            else:  # keluar
                stok_akhir = stok_awal - qty
                if stok_akhir < 0:
                    return Response(
                        {'error': f'Stok tidak cukup. Stok saat ini {stok_awal}, diminta {qty}.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            owner.qty_stok = stok_akhir
            owner.save()

            if tipe == 'masuk' and harga_beli is not None and variant is None:
                product.harga_beli = harga_beli
                product.save()

            movement = ProductStockMovement.objects.create(
                product=product,
                variant=variant,
                user=request.user,
                tipe=tipe,
                qty=qty,
                harga_beli=harga_beli,
                stok_awal=stok_awal,
                stok_akhir=stok_akhir,
                catatan=catatan,
                tanggal=tanggal,
            )

        return Response(ProductStockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='stock-in')
    def stock_in(self, request, pk=None):
        """POST /api/products/{id}/stock-in/ — Stok Masuk (qty, harga_beli opsional, catatan, tanggal)."""
        return self._apply_stock_movement(request, pk, 'masuk')

    @action(detail=True, methods=['post'], url_path='stock-out')
    def stock_out(self, request, pk=None):
        """POST /api/products/{id}/stock-out/ — Stok Keluar (qty, catatan, tanggal); ditolak jika stok jadi minus."""
        return self._apply_stock_movement(request, pk, 'keluar')

    @action(detail=True, methods=['post'], url_path='stock-opname')
    def stock_opname(self, request, pk=None):
        """POST /api/products/{id}/stock-opname/ — set stok ke jumlah fisik hasil hitung ulang."""
        qty_fisik_raw = request.data.get('qty_fisik')
        if qty_fisik_raw is None:
            return Response({'error': 'qty_fisik wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            qty_fisik = _to_decimal(qty_fisik_raw, 'qty_fisik')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if qty_fisik < 0:
            return Response({'error': 'qty_fisik tidak boleh negatif'}, status=status.HTTP_400_BAD_REQUEST)

        catatan = request.data.get('catatan', '')
        tanggal = request.data.get('tanggal')
        variant_id = request.data.get('variant')

        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=pk)
            owner, variant = self._resolve_stock_owner(product, variant_id)

            stok_awal = owner.qty_stok
            selisih = qty_fisik - stok_awal
            owner.qty_stok = qty_fisik
            owner.save()

            movement = ProductStockMovement.objects.create(
                product=product,
                variant=variant,
                user=request.user,
                tipe='opname',
                qty=abs(selisih),
                stok_awal=stok_awal,
                stok_akhir=qty_fisik,
                catatan=catatan,
                tanggal=tanggal,
            )

        return Response(ProductStockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)

class ProductImageViewSet(viewsets.ModelViewSet):
    queryset = ProductImage.objects.all().order_by('-is_primary', 'id')
    serializer_class = ProductImageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        product_id = self.request.query_params.get('product', None)
        if product_id:
            queryset = queryset.filter(product__id=product_id)
        return queryset

class ProductVariantViewSet(viewsets.ModelViewSet):
    queryset = ProductVariant.objects.all().order_by('product__nama', 'nama_varian')
    serializer_class = ProductVariantSerializer
    permission_classes = [IsAuthenticated]

class ProductPackageViewSet(viewsets.ModelViewSet):
    queryset = ProductPackage.objects.all().order_by('nama')
    serializer_class = ProductPackageSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='import-csv')
    def import_csv(self, request):
        """Import massal Paket Produk dari CSV (format resmi template Olsera):
        product_combo_name, product_name, product_variant_name, sku, description,
        purchase_price, market_price, online_selling_price, store_selling_price,
        commission, minimum_order, maximum_order, selling_prices_stores_are_dynamic,
        ready_publish_sale, sale_start_date, loyalty_points, uom.
        Baris dengan product_combo_name yang sama digabung jadi 1 paket (multi-item)."""
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        reader = csv.DictReader(io.StringIO(decoded))
        groups = {}
        order = []
        for idx, row in enumerate(reader, start=2):  # baris 1 = header
            row_lower = _csv_row_lower(row)
            combo_name = _csv_cell(row_lower, 'product_combo_name')
            if not combo_name:
                continue
            if combo_name not in groups:
                groups[combo_name] = {'fields': row_lower, 'rows': []}
                order.append(combo_name)
            groups[combo_name]['rows'].append((idx, row_lower))

        created_packages = []
        errors = []

        with transaction.atomic():
            for combo_name in order:
                group = groups[combo_name]
                f = group['fields']
                package = ProductPackage.objects.create(
                    nama=combo_name,
                    deskripsi=_csv_cell(f, 'description'),
                    harga_beli=_parse_decimal_safe(_csv_cell(f, 'purchase_price')),
                    harga_pasar=_parse_decimal_safe(_csv_cell(f, 'market_price')),
                    harga_jual_online=_parse_decimal_safe(_csv_cell(f, 'online_selling_price')),
                    harga_jual_offline=_parse_decimal_safe(_csv_cell(f, 'store_selling_price')),
                    komisi=_parse_decimal_safe(_csv_cell(f, 'commission')),
                    minimal_pesanan=_parse_int_safe(_csv_cell(f, 'minimum_order'), 1),
                    maksimal_pesanan=_parse_int_safe(_csv_cell(f, 'maximum_order'), 0),
                    harga_dinamis=_parse_bool_flag(_csv_cell(f, 'selling_prices_stores_are_dynamic')),
                    publikasi=_parse_bool_flag(_csv_cell(f, 'ready_publish_sale')),
                    periode_mulai=_parse_date_ddmmyyyy(_csv_cell(f, 'sale_start_date')),
                    loyalty_points=_parse_int_safe(_csv_cell(f, 'loyalty_points'), 0),
                    satuan=_csv_cell(f, 'uom'),
                )

                item_count = 0
                for idx, row_lower in group['rows']:
                    product_name = _csv_cell(row_lower, 'product_name')
                    sku = _csv_cell(row_lower, 'sku')

                    product = None
                    if sku:
                        product = Product.objects.filter(sku=sku).first()
                    if not product and product_name:
                        product = Product.objects.filter(nama__iexact=product_name).first()
                    if not product:
                        errors.append(f"Baris {idx} (paket '{combo_name}'): produk '{product_name or sku}' tidak ditemukan.")
                        continue

                    ProductPackageItem.objects.create(paket=package, product=product, qty=1)
                    item_count += 1

                if item_count == 0:
                    errors.append(f"Paket '{combo_name}' dibuat tanpa produk (semua baris gagal dicocokkan).")

                created_packages.append(package)

        return Response(
            {
                'created': ProductPackageSerializer(created_packages, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_packages else status.HTTP_400_BAD_REQUEST,
        )

class AddonViewSet(viewsets.ModelViewSet):
    queryset = Addon.objects.all().order_by('nama')
    serializer_class = AddonSerializer
    permission_classes = [IsAuthenticated]

class SpecificationViewSet(viewsets.ModelViewSet):
    queryset = Specification.objects.all().order_by('nama')
    serializer_class = SpecificationSerializer
    permission_classes = [IsAuthenticated]

class ProductStockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """Riwayat/Pergerakan Stok — dibuat lewat action stock-in/stock-out/stock-opname di ProductViewSet."""
    queryset = ProductStockMovement.objects.all().select_related('product', 'variant', 'user')
    serializer_class = ProductStockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        product_id = self.request.query_params.get('product', None)
        if product_id:
            queryset = queryset.filter(product__id=product_id)
        tipe = self.request.query_params.get('tipe', None)
        if tipe:
            queryset = queryset.filter(tipe=tipe)
        return queryset

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        import datetime
        from django.utils.dateparse import parse_date
        
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        
        if start_date_str:
            start_date = parse_date(start_date_str)
        else:
            start_date = datetime.date.today()
            
        if end_date_str:
            end_date = parse_date(end_date_str)
        else:
            end_date = datetime.date.today()
            
        if not start_date or not end_date:
            return Response({'error': 'Format tanggal tidak valid'}, status=400)
            
        # Ambil semua produk dan varian
        products = Product.objects.all().select_related('kategori').prefetch_related('variants')
        
        skus = {}
        for p in products:
            if p.has_variant and p.variants.exists():
                for v in p.variants.all():
                    skus[(p.id, v.id)] = {
                        'product_id': p.id,
                        'variant_id': v.id,
                        'product_name': p.nama,
                        'variant_name': v.nama_varian,
                        'sku': v.sku or p.sku or '',
                        'group': p.kategori.nama if p.kategori else 'Umum',
                        'current_qty': v.qty_stok,
                        'satuan': p.satuan,
                    }
            else:
                skus[(p.id, None)] = {
                    'product_id': p.id,
                    'variant_id': None,
                    'product_name': p.nama,
                    'variant_name': '',
                    'sku': p.sku or '',
                    'group': p.kategori.nama if p.kategori else 'Umum',
                    'current_qty': p.qty_stok,
                    'satuan': p.satuan,
                }
                
        # Ambil semua pergerakan stok hingga end_date
        movements = ProductStockMovement.objects.all().order_by('created_at')
        
        summary_map = {}
        for key, info in skus.items():
            summary_map[key] = {
                'group': info['group'],
                'product': f"{info['product_name']} - {info['variant_name']}" if info['variant_name'] else info['product_name'],
                'sku': info['sku'],
                'satuan': info['satuan'],
                'initial': float(info['current_qty']),
                'in': 0.0,
                'returnStock': 0.0,
                'sales': 0.0,
                'out': 0.0,
                'sisa': float(info['current_qty']),
                'movements_before': [],
                'movements_during': [],
            }
            
        for m in movements:
            m_date = m.tanggal or m.created_at.date()
            key = (m.product_id, m.variant_id)
            if key not in summary_map:
                continue
                
            if m_date < start_date:
                summary_map[key]['movements_before'].append(m)
            elif start_date <= m_date <= end_date:
                summary_map[key]['movements_during'].append(m)
                
        result = []
        for key, s in summary_map.items():
            info = skus[key]
            
            # Tentukan stok awal (Initial)
            if s['movements_before']:
                last_before = s['movements_before'][-1]
                initial_val = last_before.stok_akhir
            elif s['movements_during']:
                first_during = s['movements_during'][0]
                initial_val = first_during.stok_awal
            else:
                # Cari movement setelah end_date
                movements_after = ProductStockMovement.objects.filter(
                    product_id=info['product_id'],
                    variant_id=info['variant_id']
                ).order_by('created_at')
                
                after_list = []
                for m in movements_after:
                    m_date = m.tanggal or m.created_at.date()
                    if m_date > end_date:
                        after_list.append(m)
                if after_list:
                    initial_val = after_list[0].stok_awal
                else:
                    initial_val = info['current_qty']
                    
            s['initial'] = float(initial_val)
            
            # Jumlahkan mutasi selama periode
            in_qty = 0.0
            out_qty = 0.0
            sales_qty = 0.0
            return_qty = 0.0
            
            for m in s['movements_during']:
                qty = float(m.qty)
                if m.tipe in ('masuk', 'produksi'):
                    in_qty += qty
                elif m.tipe == 'keluar':
                    out_qty += qty
                elif m.tipe == 'penjualan':
                    sales_qty += qty
                elif m.tipe == 'pengembalian':
                    return_qty += qty
                    
            s['in'] = in_qty
            s['out'] = out_qty
            s['sales'] = sales_qty
            s['returnStock'] = return_qty
            
            # Tentukan sisa stok (Sisa)
            if s['movements_during']:
                sisa_val = s['movements_during'][-1].stok_akhir
            elif s['movements_before']:
                sisa_val = s['movements_before'][-1].stok_akhir
            else:
                sisa_val = initial_val
                
            s['sisa'] = float(sisa_val)
            
            # Hapus data temporer
            del s['movements_before']
            del s['movements_during']
            
            # Generate id untuk frontend
            p_id, v_id = key
            s['id'] = f"mv-{p_id}-{v_id}" if v_id else f"mv-{p_id}"
            
            result.append(s)
            
        return Response(result)

class StockInDocumentViewSet(viewsets.ModelViewSet):
    """Dokumen Stok Masuk: header + banyak item, status draft -> selesai/batal."""
    queryset = StockInDocument.objects.all().prefetch_related('items__product').select_related('dibuat_oleh')
    serializer_class = StockInDocumentSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        today = timezone.now().date()
        nomor = _next_document_number(StockInDocument, f"IN{today.strftime('%y%m%d')}")
        serializer.save(nomor=nomor, dibuat_oleh=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa diubah.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa dihapus.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='add-item')
    def add_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        product_id = request.data.get('product')
        qty_raw = request.data.get('qty')
        if not product_id or qty_raw is None:
            return Response({'error': 'product dan qty wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            qty = _to_decimal(qty_raw, 'qty')
            harga_beli = _to_decimal(request.data.get('harga_beli', 0), 'harga_beli')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if qty <= 0:
            return Response({'error': 'qty harus lebih besar dari 0'}, status=status.HTTP_400_BAD_REQUEST)

        product = get_object_or_404(Product, pk=product_id)
        rak = (request.data.get('rak') or '').strip()
        item = StockInDocumentItem.objects.create(document=document, product=product, harga_beli=harga_beli, qty=qty, rak=rak)
        return Response(StockInDocumentItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='import-csv')
    def import_csv(self, request, pk=None):
        """Import massal item dari CSV: kolom Product, Variant, SKU, Supplier, Qty, New Buy Price."""
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        reader = csv.DictReader(io.StringIO(decoded))
        created_items = []
        errors = []
        supplier_name = None

        with transaction.atomic():
            for idx, row in enumerate(reader, start=2):  # baris 1 = header
                # Cocokkan header case-insensitive; template resmi Olsera pakai
                # snake_case huruf kecil (product,variant,sku,supplier,qty,new_buy_price,rack)
                row_lower = _csv_row_lower(row)

                product_name = _csv_cell(row_lower, 'product')
                variant_name = _csv_cell(row_lower, 'variant')
                sku = _csv_cell(row_lower, 'sku')
                supplier = _csv_cell(row_lower, 'supplier')
                qty_raw = _csv_cell(row_lower, 'qty')
                harga_raw = _csv_cell(row_lower, 'new_buy_price', 'new buy price')
                rak = _csv_cell(row_lower, 'rack', 'rak')

                if supplier and not supplier_name:
                    supplier_name = supplier

                product = None
                if sku:
                    product = Product.objects.filter(sku=sku).first()
                if not product and product_name:
                    product = Product.objects.filter(nama__iexact=product_name).first()
                if not product:
                    errors.append(f"Baris {idx}: produk '{product_name or sku}' tidak ditemukan.")
                    continue

                try:
                    qty = _to_decimal(qty_raw, 'qty')
                except ValueError:
                    errors.append(f"Baris {idx}: qty '{qty_raw}' tidak valid.")
                    continue
                if qty <= 0:
                    errors.append(f"Baris {idx}: qty harus lebih besar dari 0.")
                    continue

                harga_beli = product.harga_beli
                if harga_raw:
                    try:
                        harga_beli = _to_decimal(harga_raw, 'harga_beli')
                    except ValueError:
                        errors.append(f"Baris {idx}: New Buy Price '{harga_raw}' tidak valid, memakai harga beli produk saat ini.")

                variant = None
                if variant_name:
                    variant = ProductVariant.objects.filter(product=product, nama_varian__iexact=variant_name).first()

                item = StockInDocumentItem.objects.create(
                    document=document, product=product, variant=variant,
                    harga_beli=harga_beli, qty=qty, rak=rak,
                )
                created_items.append(item)

            if supplier_name and not document.supplier:
                document.supplier = supplier_name
                document.save()

        return Response(
            {
                'document': StockInDocumentSerializer(document).data,
                'created': StockInDocumentItemSerializer(created_items, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'], url_path='remove-item')
    def remove_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)
        item_id = request.data.get('item_id')
        deleted, _ = StockInDocumentItem.objects.filter(document=document, id=item_id).delete()
        if not deleted:
            return Response({'error': 'Item tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(StockInDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='post-document')
    def post_document(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)

        # Catatan/Nama Penerima/Supplier bersifat opsional (sesuai alur Olsera asli);
        # hanya tanggal (wajib sejak dokumen dibuat) dan minimal 1 item yang diperlukan untuk posting.
        if not document.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum posting.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for item in document.items.select_related('product'):
                product = Product.objects.select_for_update().get(pk=item.product_id)
                stok_awal = product.qty_stok
                stok_akhir = stok_awal + item.qty
                product.qty_stok = stok_akhir
                if item.harga_beli:
                    product.harga_beli = item.harga_beli
                product.save()

                ProductStockMovement.objects.create(
                    product=product,
                    user=request.user,
                    tipe='masuk',
                    qty=item.qty,
                    harga_beli=item.harga_beli,
                    stok_awal=stok_awal,
                    stok_akhir=stok_akhir,
                    catatan=document.catatan,
                    tanggal=document.tanggal,
                    stock_in_document=document,
                )

            document.status = 'selesai'
            document.save()

        return Response(StockInDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Hanya dokumen draft yang bisa dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        document.status = 'batal'
        document.save()
        return Response(StockInDocumentSerializer(document).data)


class StockOutDocumentViewSet(viewsets.ModelViewSet):
    """Dokumen Stok Keluar: header + banyak item, status draft -> selesai/batal."""
    queryset = StockOutDocument.objects.all().prefetch_related('items__product').select_related('dibuat_oleh')
    serializer_class = StockOutDocumentSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        today = timezone.now().date()
        nomor = _next_document_number(StockOutDocument, f"OUT{today.strftime('%y%m%d')}")
        serializer.save(nomor=nomor, dibuat_oleh=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa diubah.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa dihapus.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='add-item')
    def add_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        product_id = request.data.get('product')
        qty_raw = request.data.get('qty')
        if not product_id or qty_raw is None:
            return Response({'error': 'product dan qty wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            qty = _to_decimal(qty_raw, 'qty')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if qty <= 0:
            return Response({'error': 'qty harus lebih besar dari 0'}, status=status.HTTP_400_BAD_REQUEST)

        product = get_object_or_404(Product, pk=product_id)
        
        # Check variant if provided
        variant_id = request.data.get('variant')
        variant = None
        if variant_id:
            variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)

        item = StockOutDocumentItem.objects.create(document=document, product=product, variant=variant, qty=qty)
        return Response(StockOutDocumentItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='remove-item')
    def remove_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)
        item_id = request.data.get('item_id')
        deleted, _ = StockOutDocumentItem.objects.filter(document=document, id=item_id).delete()
        if not deleted:
            return Response({'error': 'Item tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(StockOutDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='import-csv')
    def import_csv(self, request, pk=None):
        """Import massal item dari CSV: kolom to_store_url_id, product, variant, sku, qty."""
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        reader = csv.DictReader(io.StringIO(decoded))
        created_items = []
        errors = []
        to_store_url_id = None

        with transaction.atomic():
            for idx, row in enumerate(reader, start=2):  # baris 1 = header
                row_lower = _csv_row_lower(row)

                product_name = _csv_cell(row_lower, 'product')
                variant_name = _csv_cell(row_lower, 'variant')
                sku = _csv_cell(row_lower, 'sku')
                store_id = _csv_cell(row_lower, 'to_store_url_id', 'to store url id')
                qty_raw = _csv_cell(row_lower, 'qty')

                if store_id and not to_store_url_id:
                    to_store_url_id = store_id

                product = None
                if sku:
                    product = Product.objects.filter(sku=sku).first()
                if not product and product_name:
                    product = Product.objects.filter(nama__iexact=product_name).first()
                if not product:
                    errors.append(f"Baris {idx}: produk '{product_name or sku}' tidak ditemukan.")
                    continue

                try:
                    qty = _to_decimal(qty_raw, 'qty')
                except ValueError:
                    errors.append(f"Baris {idx}: qty '{qty_raw}' tidak valid.")
                    continue
                if qty <= 0:
                    errors.append(f"Baris {idx}: qty harus lebih besar dari 0.")
                    continue

                variant = None
                if variant_name:
                    variant = ProductVariant.objects.filter(product=product, nama_varian__iexact=variant_name).first()

                item = StockOutDocumentItem.objects.create(
                    document=document, product=product, variant=variant, qty=qty,
                )
                created_items.append(item)

            if to_store_url_id:
                document.transfer_ke = to_store_url_id
                document.alasan = 'transfer'
                document.save()

        return Response(
            {
                'document': StockOutDocumentSerializer(document).data,
                'created': StockOutDocumentItemSerializer(created_items, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'], url_path='post-document')
    def post_document(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)

        if not document.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum posting.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for item in document.items.select_related('product', 'variant'):
                if item.variant:
                    owner = ProductVariant.objects.select_for_update().get(pk=item.variant.id)
                else:
                    owner = Product.objects.select_for_update().get(pk=item.product.id)

                stok_awal = owner.qty_stok
                stok_akhir = stok_awal - item.qty
                if stok_akhir < 0:
                    return Response(
                        {'error': f"Stok produk '{owner}' tidak mencukupi. Stok saat ini {stok_awal}, diminta {item.qty}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                owner.qty_stok = stok_akhir
                owner.save()

                ProductStockMovement.objects.create(
                    product=item.product,
                    variant=item.variant,
                    user=request.user,
                    tipe='keluar',
                    qty=item.qty,
                    stok_awal=stok_awal,
                    stok_akhir=stok_akhir,
                    catatan=document.catatan,
                    tanggal=document.tanggal,
                    stock_out_document=document,
                )

            document.status = 'selesai'
            document.save()

        return Response(StockOutDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Hanya dokumen draft yang bisa dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        document.status = 'batal'
        document.save()
        return Response(StockOutDocumentSerializer(document).data)


class StockProductionDocumentViewSet(viewsets.ModelViewSet):
    """Dokumen Produksi Stok: header + banyak item, status draft -> selesai/batal.
    Sesuai template resmi Olsera: hanya menambah stok produk jadi (tanpa penyerapan bahan baku)."""
    queryset = StockProductionDocument.objects.all().prefetch_related('items__product').select_related('dibuat_oleh')
    serializer_class = StockProductionDocumentSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        today = timezone.now().date()
        nomor = _next_document_number(StockProductionDocument, f"PR{today.strftime('%y%m%d')}")
        serializer.save(nomor=nomor, dibuat_oleh=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa diubah.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa dihapus.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='add-item')
    def add_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        product_id = request.data.get('product')
        qty_raw = request.data.get('qty')
        if not product_id or qty_raw is None:
            return Response({'error': 'product dan qty wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            qty = _to_decimal(qty_raw, 'qty')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if qty <= 0:
            return Response({'error': 'qty harus lebih besar dari 0'}, status=status.HTTP_400_BAD_REQUEST)

        product = get_object_or_404(Product, pk=product_id)

        variant_id = request.data.get('variant')
        variant = None
        if variant_id:
            variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)

        item = StockProductionDocumentItem.objects.create(document=document, product=product, variant=variant, qty=qty)
        return Response(StockProductionDocumentItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='remove-item')
    def remove_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)
        item_id = request.data.get('item_id')
        deleted, _ = StockProductionDocumentItem.objects.filter(document=document, id=item_id).delete()
        if not deleted:
            return Response({'error': 'Item tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(StockProductionDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='import-csv')
    def import_csv(self, request, pk=None):
        """Import massal item dari CSV: kolom Product, Variant Name, Qty (template resmi Olsera)."""
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        reader = csv.DictReader(io.StringIO(decoded))
        created_items = []
        errors = []

        with transaction.atomic():
            for idx, row in enumerate(reader, start=2):  # baris 1 = header
                row_lower = _csv_row_lower(row)

                product_name = _csv_cell(row_lower, 'product')
                variant_name = _csv_cell(row_lower, 'variant name', 'variant')
                qty_raw = _csv_cell(row_lower, 'qty')

                product = Product.objects.filter(nama__iexact=product_name).first() if product_name else None
                if not product:
                    errors.append(f"Baris {idx}: produk '{product_name}' tidak ditemukan.")
                    continue

                try:
                    qty = _to_decimal(qty_raw, 'qty')
                except ValueError:
                    errors.append(f"Baris {idx}: qty '{qty_raw}' tidak valid.")
                    continue
                if qty <= 0:
                    errors.append(f"Baris {idx}: qty harus lebih besar dari 0.")
                    continue

                variant = None
                if variant_name:
                    variant = ProductVariant.objects.filter(product=product, nama_varian__iexact=variant_name).first()

                item = StockProductionDocumentItem.objects.create(
                    document=document, product=product, variant=variant, qty=qty,
                )
                created_items.append(item)

        return Response(
            {
                'document': StockProductionDocumentSerializer(document).data,
                'created': StockProductionDocumentItemSerializer(created_items, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'], url_path='post-document')
    def post_document(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)

        if not document.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum posting.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for item in document.items.select_related('product', 'variant'):
                if item.variant:
                    owner = ProductVariant.objects.select_for_update().get(pk=item.variant.id)
                else:
                    owner = Product.objects.select_for_update().get(pk=item.product.id)

                stok_awal = owner.qty_stok
                stok_akhir = stok_awal + item.qty
                owner.qty_stok = stok_akhir
                owner.save()

                ProductStockMovement.objects.create(
                    product=item.product,
                    variant=item.variant,
                    user=request.user,
                    tipe='produksi',
                    qty=item.qty,
                    stok_awal=stok_awal,
                    stok_akhir=stok_akhir,
                    catatan=document.catatan,
                    tanggal=document.tanggal,
                    stock_production_document=document,
                )

            document.status = 'selesai'
            document.save()

        return Response(StockProductionDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Hanya dokumen draft yang bisa dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        document.status = 'batal'
        document.save()
        return Response(StockProductionDocumentSerializer(document).data)


class StockOpnameDocumentViewSet(viewsets.ModelViewSet):
    """Dokumen Stok Opname: header + banyak item, status draft -> selesai/batal.
    Posting menimpa qty_stok produk dengan qty aktual hasil hitung fisik (bukan menambah/mengurangi)."""
    queryset = StockOpnameDocument.objects.all().prefetch_related('items__product').select_related('dibuat_oleh')
    serializer_class = StockOpnameDocumentSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        today = timezone.now().date()
        nomor = _next_document_number(StockOpnameDocument, f"OP{today.strftime('%y%m%d')}")
        serializer.save(nomor=nomor, dibuat_oleh=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa diubah.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa dihapus.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @staticmethod
    def _template_rows():
        """Daftar baris template (1 baris per produk, atau per varian bila produk punya varian),
        urutan tetap berdasar id produk agar rentang 'Baris N - M' konsisten antar request."""
        rows = []
        for product in Product.objects.all().order_by('id').prefetch_related('variants'):
            variants = list(product.variants.all())
            if product.has_variant and variants:
                for variant in variants:
                    rows.append({
                        'time': '', 'product': product.nama, 'variant': variant.nama_varian,
                        'sku': variant.sku or product.sku or '', 'qty': '', 'rack': '',
                    })
            else:
                rows.append({
                    'time': '', 'product': product.nama, 'variant': '',
                    'sku': product.sku or '', 'qty': '', 'rack': '',
                })
        return rows

    @action(detail=False, methods=['get'], url_path='template-csv')
    def template_csv(self, request):
        """Template import berisi daftar produk asli (kolom qty & rack kosong utk diisi),
        dipaginasi per rentang baris seperti fitur 'Download Template' Olsera
        (dropdown rentang di frontend tetap/statis, tidak tergantung jumlah produk saat ini —
        rentang yang melebihi jumlah produk asli cukup menghasilkan CSV kosong/sebagian)."""
        rows = self._template_rows()

        try:
            start = max(1, int(request.query_params.get('start', 1)))
            end = int(request.query_params.get('end', start + 499))
        except (TypeError, ValueError):
            return Response({'error': 'start/end harus berupa angka'}, status=status.HTTP_400_BAD_REQUEST)
        if end - start + 1 > 500:
            return Response({'error': 'Maksimal 500 baris per file template.'}, status=status.HTTP_400_BAD_REQUEST)

        page_rows = rows[start - 1:end]

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=['time', 'product', 'variant', 'sku', 'qty', 'rack'])
        writer.writeheader()
        writer.writerows(page_rows)

        response = HttpResponse(buf.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="stockopname_template_{start}_{end}.csv"'
        return response

    @action(detail=True, methods=['post'], url_path='add-item')
    def add_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        product_id = request.data.get('product')
        stok_aktual_raw = request.data.get('stok_aktual')
        if not product_id or stok_aktual_raw is None:
            return Response({'error': 'product dan stok_aktual wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            stok_aktual = _to_decimal(stok_aktual_raw, 'stok_aktual')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if stok_aktual < 0:
            return Response({'error': 'stok_aktual tidak boleh negatif'}, status=status.HTTP_400_BAD_REQUEST)

        product = get_object_or_404(Product, pk=product_id)

        variant_id = request.data.get('variant')
        variant = None
        owner = product
        if variant_id:
            variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)
            owner = variant

        jam_opname = (request.data.get('jam_opname') or '').strip()
        rak = (request.data.get('rak') or '').strip()
        tanggal_kadaluwarsa = request.data.get('tanggal_kadaluwarsa') or None
        item = StockOpnameDocumentItem.objects.create(
            document=document, product=product, variant=variant,
            jam_opname=jam_opname, rak=rak, tanggal_kadaluwarsa=tanggal_kadaluwarsa,
            stok_sistem=owner.qty_stok, stok_aktual=stok_aktual,
        )
        return Response(StockOpnameDocumentItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='bulk-add-items')
    def bulk_add_items(self, request, pk=None):
        """Tambah banyak produk sekaligus (multi-select ala 'Tambah Produk' Olsera).
        stok_aktual default 0 — diisi belakangan satu-satu lewat update-item.
        Maksimal 500 produk per panggilan, sama seperti batasan Olsera."""
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        products_payload = request.data.get('products')
        if not isinstance(products_payload, list) or not products_payload:
            return Response({'error': 'products wajib diisi (list produk).'}, status=status.HTTP_400_BAD_REQUEST)
        if len(products_payload) > 500:
            return Response({'error': f'Maksimal 500 produk terpilih tiap penambahan (dikirim {len(products_payload)}).'}, status=status.HTTP_400_BAD_REQUEST)

        jam_opname = (request.data.get('jam_opname') or '').strip()
        created_items = []
        errors = []

        with transaction.atomic():
            for entry in products_payload:
                product_id = entry.get('product') if isinstance(entry, dict) else entry
                variant_id = entry.get('variant') if isinstance(entry, dict) else None
                try:
                    product = Product.objects.get(pk=product_id)
                except Product.DoesNotExist:
                    errors.append(f"Produk id {product_id} tidak ditemukan.")
                    continue

                variant = None
                owner = product
                if variant_id:
                    try:
                        variant = ProductVariant.objects.get(pk=variant_id, product=product)
                        owner = variant
                    except ProductVariant.DoesNotExist:
                        errors.append(f"Varian id {variant_id} tidak ditemukan untuk produk '{product.nama}'.")
                        continue

                item = StockOpnameDocumentItem.objects.create(
                    document=document, product=product, variant=variant,
                    jam_opname=jam_opname, stok_sistem=owner.qty_stok, stok_aktual=0,
                )
                created_items.append(item)

        return Response(
            {
                'document': StockOpnameDocumentSerializer(document).data,
                'created': StockOpnameDocumentItemSerializer(created_items, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'], url_path='update-item')
    def update_item(self, request, pk=None):
        """Isi/ubah Qty Aktual, Jam, Rak, atau Tgl Kadaluwarsa item yang sudah ditambahkan
        (dipakai setelah bulk-add-items, mengisi hitungan fisik satu-satu)."""
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        item_id = request.data.get('item_id')
        item = StockOpnameDocumentItem.objects.filter(document=document, id=item_id).first()
        if not item:
            return Response({'error': 'Item tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        if 'stok_aktual' in request.data:
            try:
                stok_aktual = _to_decimal(request.data.get('stok_aktual'), 'stok_aktual')
            except ValueError as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            if stok_aktual < 0:
                return Response({'error': 'stok_aktual tidak boleh negatif'}, status=status.HTTP_400_BAD_REQUEST)
            item.stok_aktual = stok_aktual
        if 'jam_opname' in request.data:
            item.jam_opname = (request.data.get('jam_opname') or '').strip()
        if 'rak' in request.data:
            item.rak = (request.data.get('rak') or '').strip()
        if 'tanggal_kadaluwarsa' in request.data:
            item.tanggal_kadaluwarsa = request.data.get('tanggal_kadaluwarsa') or None
        item.save()

        return Response(StockOpnameDocumentItemSerializer(item).data)

    @action(detail=True, methods=['post'], url_path='remove-item')
    def remove_item(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)
        item_id = request.data.get('item_id')
        deleted, _ = StockOpnameDocumentItem.objects.filter(document=document, id=item_id).delete()
        if not deleted:
            return Response({'error': 'Item tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(StockOpnameDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='import-csv')
    def import_csv(self, request, pk=None):
        """Import massal item dari CSV: kolom time, product, variant, sku, qty, rack (template resmi Olsera).
        Qty di sini adalah hasil hitung fisik per baris/rak (stok aktual); produk yang sama boleh
        muncul di beberapa baris (rak berbeda) — dijumlah saat posting, lihat post_document()."""
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > 500:
            return Response({'error': f'Maksimal 500 baris per file import (file ini {len(rows)} baris).'}, status=status.HTTP_400_BAD_REQUEST)

        created_items = []
        errors = []

        with transaction.atomic():
            for idx, row in enumerate(rows, start=2):  # baris 1 = header
                row_lower = _csv_row_lower(row)

                time_val = _csv_cell(row_lower, 'time')
                product_name = _csv_cell(row_lower, 'product')
                variant_name = _csv_cell(row_lower, 'variant')
                sku = _csv_cell(row_lower, 'sku')
                qty_raw = _csv_cell(row_lower, 'qty')
                rak = _csv_cell(row_lower, 'rack', 'rak')

                product = None
                if sku:
                    product = Product.objects.filter(sku=sku).first()
                if not product and product_name:
                    product = Product.objects.filter(nama__iexact=product_name).first()
                if not product:
                    errors.append(f"Baris {idx}: produk '{product_name or sku}' tidak ditemukan.")
                    continue

                try:
                    stok_aktual = _to_decimal(qty_raw, 'qty')
                except ValueError:
                    errors.append(f"Baris {idx}: qty '{qty_raw}' tidak valid.")
                    continue
                if stok_aktual < 0:
                    errors.append(f"Baris {idx}: qty tidak boleh negatif.")
                    continue

                variant = None
                owner = product
                if variant_name:
                    variant = ProductVariant.objects.filter(product=product, nama_varian__iexact=variant_name).first()
                    if variant:
                        owner = variant

                item = StockOpnameDocumentItem.objects.create(
                    document=document, product=product, variant=variant,
                    jam_opname=time_val, rak=rak, stok_sistem=owner.qty_stok, stok_aktual=stok_aktual,
                )
                created_items.append(item)

        return Response(
            {
                'document': StockOpnameDocumentSerializer(document).data,
                'created': StockOpnameDocumentItemSerializer(created_items, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'], url_path='post-document')
    def post_document(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Dokumen sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)

        if not document.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum posting.'}, status=status.HTTP_400_BAD_REQUEST)

        # Produk yang sama boleh muncul di beberapa baris (rak berbeda) — jumlahkan
        # stok_aktual per (product, variant) dulu sebelum menimpa qty_stok, supaya
        # baris kedua tidak menghapus hasil hitung baris pertama untuk produk yang sama.
        groups = {}
        for item in document.items.select_related('product', 'variant'):
            key = (item.product_id, item.variant_id)
            if key not in groups:
                groups[key] = {'product': item.product, 'variant': item.variant, 'total': Decimal('0')}
            groups[key]['total'] += item.stok_aktual

        with transaction.atomic():
            for group in groups.values():
                if group['variant']:
                    owner = ProductVariant.objects.select_for_update().get(pk=group['variant'].id)
                else:
                    owner = Product.objects.select_for_update().get(pk=group['product'].id)

                stok_awal = owner.qty_stok
                stok_akhir = group['total']
                owner.qty_stok = stok_akhir
                owner.save()

                ProductStockMovement.objects.create(
                    product=group['product'],
                    variant=group['variant'],
                    user=request.user,
                    tipe='opname',
                    qty=abs(stok_akhir - stok_awal),
                    stok_awal=stok_awal,
                    stok_akhir=stok_akhir,
                    catatan=document.catatan,
                    tanggal=document.tanggal,
                    stock_opname_document=document,
                )

            document.status = 'selesai'
            document.save()

        return Response(StockOpnameDocumentSerializer(document).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        document = self.get_object()
        if document.status != 'draft':
            return Response({'error': 'Hanya dokumen draft yang bisa dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        document.status = 'batal'
        document.save()
        return Response(StockOpnameDocumentSerializer(document).data)
