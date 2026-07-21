import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from api.permissions import IsOwnerManagerAdminOrReadOnly
from rest_framework.response import Response

from .product_models import (
    ProductCategory, Brand, SpecialType, Collection,
    Product, ProductVariant, ProductPackage, ProductPackageItem, Addon, Specification, ProductSpecValue,
    ProductStockMovement, ProductImage, StockInDocument, StockInDocumentItem,
    StockOutDocument, StockOutDocumentItem, StockProductionDocument, StockProductionDocumentItem,
    StockOpnameDocument, StockOpnameDocumentItem, ProductActivityLog,
    Purchase, PurchaseItem, PurchasePayment,
    StockLayer, StockLayerConsumption
)
from .product_serializers import (
    ProductCategorySerializer, BrandSerializer, SpecialTypeSerializer,
    CollectionSerializer, ProductSerializer, ProductVariantSerializer,
    ProductPackageSerializer, AddonSerializer, SpecificationSerializer, ProductSpecValueSerializer,
    ProductStockMovementSerializer, ProductImageSerializer,
    StockInDocumentSerializer, StockInDocumentItemSerializer,
    StockOutDocumentSerializer, StockOutDocumentItemSerializer,
    StockProductionDocumentSerializer, StockProductionDocumentItemSerializer,
    StockOpnameDocumentSerializer, StockOpnameDocumentItemSerializer,
    PurchaseSerializer, PurchaseItemSerializer, PurchasePaymentSerializer
)
from .models import BillOfMaterials, BoMItem
from .customer_models import Supplier
from . import production_costing
from . import stock_fifo
from . import uom
from . import pos_settings

# Batas baris per import CSV. Tanpa batas, satu file besar diproses dalam satu
# transaction.atomic() dan berisiko timeout / lock tabel produk berkepanjangan.
# Angkanya mengikuti Olsera dan harus sama dengan maxRows di frontend supaya user
# tidak ditolak server setelah pratinjau terlanjur menyatakan aman.
CSV_IMPORT_MAX_ROWS = 200            # Stok Masuk
CSV_IMPORT_MAX_ROWS_STOCK_OUT = 500  # Stok Keluar


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
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class BrandViewSet(viewsets.ModelViewSet):
    queryset = Brand.objects.all().order_by('nama')
    serializer_class = BrandSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class SpecialTypeViewSet(viewsets.ModelViewSet):
    queryset = SpecialType.objects.prefetch_related('products_multi').all().order_by('urutan')
    serializer_class = SpecialTypeSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class CollectionViewSet(viewsets.ModelViewSet):
    queryset = Collection.objects.all().order_by('nama')
    serializer_class = CollectionSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class ProductViewSet(viewsets.ModelViewSet):
    # ProductSerializer mengekspos kategori/brand/koleksi + varian & gambar.
    # Tanpa select/prefetch, tiap produk memicu beberapa query tambahan.
    queryset = (
        Product.objects
        .select_related('kategori', 'brand', 'koleksi')
        .prefetch_related('variants', 'fotos', 'tipe_specials')
        .order_by('-created_at')
    )
    serializer_class = ProductSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get('kategori', None)
        if category is not None:
            queryset = queryset.filter(kategori__id=category)
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(nama__icontains=search) |
                Q(nama_alternatif__icontains=search) |
                Q(sku__icontains=search) |
                Q(barcode__icontains=search) |
                Q(variants__nama_varian__icontains=search) |
                Q(variants__sku__icontains=search) |
                Q(variants__barcode__icontains=search)
            )
        special_type = self.request.query_params.get('tipe_special')
        if special_type:
            queryset = queryset.filter(
                Q(tipe_specials__id=special_type) | Q(tipe_special_id=special_type)
            )
        return queryset.distinct()

    def destroy(self, request, *args, **kwargs):
        """Hormati setelan Pengaturan POS > Cek Stok: cegah hapus produk yang
        stoknya masih ada, supaya nilai persediaan tidak hilang diam-diam."""
        instance = self.get_object()
        if pos_settings.blokir_hapus_produk_jika_ada_stok():
            sisa = float(instance.qty_stok or 0)
            if sisa > 0:
                return Response(
                    {'error': f"'{instance.nama}' masih memiliki stok {sisa:g}. "
                              f"Aturan 'blokir hapus produk jika stok masih ada' aktif di Pengaturan POS."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        product = serializer.save()
        ProductActivityLog.objects.create(
            product=product,
            user=self.request.user,
            aksi="Menambahkan produk",
            catatan=f"Produk '{product.nama}' berhasil dibuat."
        )

    def perform_update(self, serializer):
        old_product = self.get_object()
        
        old_tersedia_online = old_product.tersedia_online
        old_tidak_tersedia_offline = old_product.tidak_tersedia_offline_pos
        old_harga_jual = old_product.harga_jual_toko
        
        product = serializer.save()
        
        changes = []
        if old_tersedia_online != product.tersedia_online:
            status_str = "Tersedia" if product.tersedia_online else "Tidak Tersedia"
            changes.append(f"Mengubah ketersediaan online menjadi {status_str}")
        if old_tidak_tersedia_offline != product.tidak_tersedia_offline_pos:
            status_str = "Tidak Tersedia" if product.tidak_tersedia_offline_pos else "Tersedia"
            changes.append(f"Mengubah ketersediaan offline (POS) menjadi {status_str}")
        if old_harga_jual != product.harga_jual_toko:
            changes.append(f"Mengubah harga jual dari Rp. {old_harga_jual:,.2f} menjadi Rp. {product.harga_jual_toko:,.2f}".replace(",", "."))
            
        if not changes:
            changes.append("Memperbarui detail produk")
            
        for change in changes:
            ProductActivityLog.objects.create(
                product=product,
                user=self.request.user,
                aksi=change
            )

    @action(detail=True, methods=['post'], url_path='copy')
    def copy_product(self, request, pk=None):
        original = self.get_object()
        data = request.data
        
        # Helper to parse FK IDs robustly
        def _resolve_fk_id(key, default_val):
            if key in data:
                val = data.get(key)
                if val is None or str(val).strip() == '' or str(val).lower() == 'null':
                    return None
                try:
                    return int(val)
                except (ValueError, TypeError):
                    return None
            return default_val

        # Extract form parameters
        nama = data.get('nama', original.nama)
        nama_alternatif = data.get('nama_alternatif', original.nama_alternatif)
        harga_jual_online = data.get('harga_jual_online', original.harga_jual_online)
        harga_online_sama = data.get('harga_online_sama', original.harga_online_sama)
        lacak_inventori = data.get('lacak_inventori', original.lacak_inventori)
        rack = data.get('rack', original.rack)
        qty_stok = data.get('qty_stok', 0.00)
        stok_minimum = data.get('stok_minimum', original.stok_minimum)
        qty_fast_moving = data.get('qty_fast_moving', original.qty_fast_moving)
        
        # Optional detail fields
        sku = data.get('sku', None)
        barcode = data.get('barcode', None)
        kondisi = data.get('kondisi', original.kondisi)
        deskripsi = data.get('deskripsi', original.deskripsi)
        catatan = data.get('catatan', original.catatan)
        harga_dinamis = data.get('harga_dinamis', original.harga_dinamis)
        satuan = data.get('satuan', original.satuan)
        butuh_pengiriman = data.get('butuh_pengiriman', original.butuh_pengiriman)
        berat = data.get('berat', original.berat)
        bebas_pajak = data.get('bebas_pajak', original.bebas_pajak)
        bebas_biaya_layanan = data.get('bebas_biaya_layanan', original.bebas_biaya_layanan)
        tersedia_online = data.get('tersedia_online', original.tersedia_online)
        tanggal_tersedia_online = data.get('tanggal_tersedia_online', original.tanggal_tersedia_online)
        tidak_tersedia_offline_pos = data.get('tidak_tersedia_offline_pos', original.tidak_tersedia_offline_pos)
        meta_keywords = data.get('meta_keywords', original.meta_keywords)
        meta_description = data.get('meta_description', original.meta_description)
        
        kategori_id = _resolve_fk_id('kategori_id', original.kategori.id if original.kategori else None)
        brand_id = _resolve_fk_id('brand_id', original.brand.id if original.brand else None)
        koleksi_id = _resolve_fk_id('koleksi_id', original.koleksi.id if original.koleksi else None)
        
        copy_photo = data.get('copy_photo', False)
        copy_variant = data.get('copy_variant', False)
        copy_tiers = data.get('copy_tiers', False)
        copy_bom = data.get('copy_bom', False)
        
        with transaction.atomic():
            # Create the duplicated product
            new_product = Product.objects.create(
                nama=nama,
                nama_alternatif=nama_alternatif,
                klasifikasi=original.klasifikasi,
                kondisi=kondisi,
                bebas_pajak=bebas_pajak,
                bebas_biaya_layanan=bebas_biaya_layanan,
                kategori_id=kategori_id,
                brand_id=brand_id,
                koleksi_id=koleksi_id,
                tipe_special=original.tipe_special,
                satuan=satuan,
                price_type=original.price_type,
                tiers=original.tiers if copy_tiers else None,
                harga_beli=original.harga_beli,
                harga_pasar=original.harga_pasar,
                harga_jual_toko=original.harga_jual_toko,
                harga_jual_online=harga_jual_online,
                harga_online_sama=harga_online_sama,
                harga_dinamis=harga_dinamis,
                komisi=original.komisi,
                minimal_pesanan=original.minimal_pesanan,
                maksimal_pesanan=original.maksimal_pesanan,
                lacak_inventori=lacak_inventori,
                rack=rack,
                qty_stok=qty_stok,
                stok_minimum=stok_minimum,
                qty_fast_moving=qty_fast_moving,
                has_variant=original.has_variant if copy_variant else False,
                tersedia_online=tersedia_online,
                tanggal_tersedia_online=tanggal_tersedia_online if tanggal_tersedia_online else None,
                tidak_tersedia_offline_pos=tidak_tersedia_offline_pos,
                butuh_pengiriman=butuh_pengiriman,
                pesanan_no_seri=original.pesanan_no_seri,
                kategori_unggulan=original.kategori_unggulan,
                kategori_sale=original.kategori_sale,
                kategori_preorder=original.kategori_preorder,
                kategori_rilis_terbaru=original.kategori_rilis_terbaru,
                kategori_populer=original.kategori_populer,
                kategori_bahan_mentah=original.kategori_bahan_mentah,
                material=original.material,
                berat=berat,
                deskripsi=deskripsi,
                catatan=catatan,
                meta_keywords=meta_keywords,
                meta_description=meta_description,
                is_active=original.is_active
            )
            
            # SKU handling
            final_sku = sku if sku else None
            if final_sku:
                new_product.sku = final_sku
                if Product.objects.filter(sku=new_product.sku).exists():
                    new_product.sku = f"{final_sku}-COPY"
                    counter = 1
                    while Product.objects.filter(sku=new_product.sku).exists():
                        new_product.sku = f"{final_sku}-COPY{counter}"
                        counter += 1
            elif original.sku:
                new_product.sku = f"{original.sku}-COPY"
                counter = 1
                while Product.objects.filter(sku=new_product.sku).exists():
                    new_product.sku = f"{original.sku}-COPY{counter}"
                    counter += 1
            
            # Barcode handling
            final_barcode = barcode if barcode else None
            if final_barcode:
                new_product.barcode = final_barcode
                if Product.objects.filter(barcode=new_product.barcode).exists():
                    new_product.barcode = f"{final_barcode}-COPY"
                    counter = 1
                    while Product.objects.filter(barcode=new_product.barcode).exists():
                        new_product.barcode = f"{final_barcode}-COPY{counter}"
                        counter += 1
            elif original.barcode:
                new_product.barcode = f"{original.barcode}-COPY"
                counter = 1
                while Product.objects.filter(barcode=new_product.barcode).exists():
                    new_product.barcode = f"{original.barcode}-COPY{counter}"
                    counter += 1
            
            new_product.save()

            # M2M hanya bisa di-set setelah instance punya PK. Tanpa baris ini
            # hasil duplikat selalu kehilangan tipe special-nya, karena UI
            # menulis lewat tipe_specials, bukan FK lama tipe_special.
            new_product.tipe_specials.set(original.tipe_specials.all())

            # Copy photos
            if copy_photo:
                for img in original.images.all():
                    ProductImage.objects.create(
                        product=new_product,
                        image=img.image,
                        is_primary=img.is_primary
                    )
            
            # Copy variants
            if copy_variant and original.has_variant:
                for var in original.variants.all():
                    # Create new variant
                    new_var = ProductVariant.objects.create(
                        product=new_product,
                        nama_varian=var.nama_varian,
                        sku=f"{var.sku}-COPY" if var.sku else None,
                        barcode=f"{var.barcode}-COPY" if var.barcode else None,
                        harga_beli=var.harga_beli,
                        harga_jual_toko=var.harga_jual_toko,
                        harga_jual_online=var.harga_jual_online,
                        lacak_inventori=lacak_inventori,
                        rack=rack if rack else var.rack,
                        qty_stok=qty_stok if not var.lacak_inventori else 0.00,
                        stok_minimum=var.stok_minimum,
                        qty_fast_moving=var.qty_fast_moving,
                        is_active=var.is_active
                    )
                    # Uniqueness checks for variant sku/barcode
                    if new_var.sku:
                        counter = 1
                        while ProductVariant.objects.filter(sku=new_var.sku).exists():
                            new_var.sku = f"{var.sku}-COPY{counter}"
                            counter += 1
                    if new_var.barcode:
                        counter = 1
                        while ProductVariant.objects.filter(barcode=new_var.barcode).exists():
                            new_var.barcode = f"{var.barcode}-COPY{counter}"
                            counter += 1
                    new_var.save()
            
            # Copy BOM (Bill of Materials) / Recipes
            if copy_bom:
                for bom in BillOfMaterials.objects.filter(product=original):
                    new_bom = BillOfMaterials.objects.create(
                        product=new_product,
                        nama=f"{bom.nama} (Copy)",
                        deskripsi=bom.deskripsi,
                        porsi_output=bom.porsi_output,
                        is_active=bom.is_active
                    )
                    for item in bom.items.all():
                        BoMItem.objects.create(
                            bom=new_bom,
                            inventory_item=item.inventory_item,
                            qty_dibutuhkan=item.qty_dibutuhkan,
                            satuan=item.satuan
                        )
            
        serializer = self.get_serializer(new_product)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='import-products')
    def import_products(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File tidak ditemukan / tidak terunggah.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            data = file_obj.read().decode('utf-8')
        except UnicodeDecodeError:
            try:
                data = file_obj.read().decode('latin-1')
            except Exception as e:
                return Response({'error': f'Gagal membaca file: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
                
        csv_file = io.StringIO(data)
        reader = csv.DictReader(csv_file)
        
        # Baca semua baris
        rows = list(reader)
        if not rows:
            return Response({'error': 'File CSV kosong.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Group berdasarkan nama produk
        from collections import defaultdict
        product_groups = defaultdict(list)
        for row in rows:
            name = row.get('name')
            if name:
                product_groups[name.strip()].append(row)
                
        created_count = 0
        updated_count = 0
        
        with transaction.atomic():
            for product_name, group_rows in product_groups.items():
                first_row = group_rows[0]
                
                # Resolusi Kategori
                category_name = first_row.get('category')
                category_obj = None
                if category_name:
                    category_obj, _ = ProductCategory.objects.get_or_create(nama=category_name.strip())
                    
                # Resolusi Brand
                brand_name = first_row.get('brand')
                brand_obj = None
                if brand_name:
                    brand_obj, _ = Brand.objects.get_or_create(nama=brand_name.strip())
                    
                # Resolusi Koleksi
                collection_name = first_row.get('collections')
                collection_obj = None
                if collection_name:
                    c_name = collection_name.split(',')[0].strip()
                    if c_name:
                        collection_obj, _ = Collection.objects.get_or_create(nama=c_name)
                        
                # Cek varian
                has_var = any(r.get('variant_names') for r in group_rows)
                
                product_obj = Product.objects.filter(nama=product_name).first()
                is_new = product_obj is None
                
                if is_new:
                    product_obj = Product(nama=product_name)
                    
                product_obj.nama_alternatif = first_row.get('alternative_name') or ""
                product_obj.kategori = category_obj
                product_obj.brand = brand_obj
                product_obj.koleksi = collection_obj
                product_obj.satuan = first_row.get('uom') or "pcs"
                product_obj.deskripsi = first_row.get('description') or ""
                product_obj.has_variant = has_var
                product_obj.is_active = (first_row.get('published', '1') == '1')
                
                if not has_var:
                    product_obj.sku = first_row.get('sku') or None
                    product_obj.barcode = first_row.get('barcode') or None
                    try:
                        product_obj.harga_beli = Decimal(first_row.get('buy_price') or '0.00')
                    except (InvalidOperation, ValueError):
                        product_obj.harga_beli = Decimal('0.00')
                    try:
                        product_obj.harga_jual_toko = Decimal(first_row.get('pos_sell_price') or first_row.get('sell_price') or '0.00')
                    except (InvalidOperation, ValueError):
                        product_obj.harga_jual_toko = Decimal('0.00')
                    try:
                        product_obj.harga_jual_online = Decimal(first_row.get('sell_price') or '0.00')
                    except (InvalidOperation, ValueError):
                        product_obj.harga_jual_online = Decimal('0.00')
                    product_obj.harga_online_sama = (product_obj.harga_jual_toko == product_obj.harga_jual_online)
                    product_obj.lacak_inventori = (first_row.get('track_inventory', '1') == '1')
                    try:
                        product_obj.qty_stok = Decimal(first_row.get('stock_qty') or '0.00')
                    except (InvalidOperation, ValueError):
                        product_obj.qty_stok = Decimal('0.00')
                    try:
                        product_obj.stok_minimum = Decimal(first_row.get('low_stock_alert') or '0.00')
                    except (InvalidOperation, ValueError):
                        product_obj.stok_minimum = Decimal('0.00')
                    try:
                        product_obj.qty_fast_moving = Decimal(first_row.get('qty_fast_moving') or '0.00')
                    except (InvalidOperation, ValueError):
                        product_obj.qty_fast_moving = Decimal('0.00')
                    product_obj.rack = first_row.get('rack') or ""
                else:
                    # Bersihkan SKU/barcode agar tidak bentrok
                    product_obj.sku = None
                    product_obj.barcode = None
                    
                product_obj.save()
                
                if is_new:
                    created_count += 1
                else:
                    updated_count += 1
                    
                if has_var:
                    for r in group_rows:
                        v_name = r.get('variant_names')
                        if not v_name:
                            continue
                            
                        variant_obj = ProductVariant.objects.filter(product=product_obj, nama_varian=v_name).first()
                        if not variant_obj:
                            variant_obj = ProductVariant(product=product_obj, nama_varian=v_name)
                            
                        variant_obj.nama_alternatif = r.get('alternative_variant_name') or ""
                        variant_obj.sku = r.get('sku') or None
                        variant_obj.barcode = r.get('barcode') or None
                        try:
                            variant_obj.harga_beli = Decimal(r.get('buy_price') or '0.00')
                        except (InvalidOperation, ValueError):
                            variant_obj.harga_beli = Decimal('0.00')
                        try:
                            variant_obj.harga_jual_toko = Decimal(r.get('pos_sell_price') or r.get('sell_price') or '0.00')
                        except (InvalidOperation, ValueError):
                            variant_obj.harga_jual_toko = Decimal('0.00')
                        try:
                            variant_obj.harga_jual_online = Decimal(r.get('sell_price') or '0.00')
                        except (InvalidOperation, ValueError):
                            variant_obj.harga_jual_online = Decimal('0.00')
                        try:
                            variant_obj.harga_pasar = Decimal(r.get('market_price') or '0.00')
                        except (InvalidOperation, ValueError):
                            variant_obj.harga_pasar = Decimal('0.00')
                        try:
                            variant_obj.qty_stok = Decimal(r.get('stock_qty') or '0.00')
                        except (InvalidOperation, ValueError):
                            variant_obj.qty_stok = Decimal('0.00')
                        variant_obj.rack = r.get('rack') or ""
                        variant_obj.lacak_inventori = (r.get('track_inventory', '1') == '1')
                        
                        try:
                            w_kg = Decimal(r.get('weight_kg') or '0')
                        except (InvalidOperation, ValueError):
                            w_kg = Decimal('0')
                        variant_obj.berat = w_kg * 1000
                        
                        variant_obj.save()
                        
        return Response({
            'success': True,
            'message': f'Produk berhasil diimpor. Baru: {created_count}, Diperbarui: {updated_count}'
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='import-recipes')
    def import_recipes(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File tidak ditemukan / tidak terunggah.'}, status=status.HTTP_400_BAD_REQUEST)
            
        from .models import ProductPrice, BillOfMaterials, BoMItem, InventoryItem
        
        try:
            data = file_obj.read().decode('utf-8')
        except UnicodeDecodeError:
            try:
                data = file_obj.read().decode('latin-1')
            except Exception as e:
                return Response({'error': f'Gagal membaca file: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
                
        csv_file = io.StringIO(data)
        reader = csv.DictReader(csv_file)
        
        rows = list(reader)
        if not rows:
            return Response({'error': 'File CSV kosong.'}, status=status.HTTP_400_BAD_REQUEST)
            
        imported_count = 0
        
        with transaction.atomic():
            for row in rows:
                product_name = row.get('product_name')
                if not product_name:
                    continue
                product_name = product_name.strip()
                
                product_variant_name = row.get('product_variant_name')
                if product_variant_name:
                    product_variant_name = product_variant_name.strip()
                    if product_variant_name == '0':
                        product_variant_name = None
                else:
                    product_variant_name = None
                    
                # Temukan atau buat ProductPrice
                product_price_obj = ProductPrice.objects.filter(nama_produk=product_name, material=product_variant_name).first()
                if not product_price_obj:
                    if not product_variant_name:
                        product_price_obj = ProductPrice.objects.filter(nama_produk=product_name).first()
                    
                    if not product_price_obj:
                        product_price_obj = ProductPrice.objects.create(
                            kategori="Umum",
                            nama_produk=product_name,
                            material=product_variant_name,
                            harga=0
                        )
                
                # Temukan atau buat BillOfMaterials
                bom_obj, _ = BillOfMaterials.objects.get_or_create(
                    product=product_price_obj,
                    defaults={'nama': f"BoM {product_price_obj.nama_produk}" + (f" - {product_price_obj.material}" if product_price_obj.material else "")}
                )
                
                # Temukan atau buat InventoryItem
                mat_name = row.get('material_product_name')
                if not mat_name:
                    continue
                mat_name = mat_name.strip()
                
                mat_var = row.get('material_variant_name')
                if mat_var:
                    mat_var = mat_var.strip()
                    if mat_var == '0':
                        mat_var = None
                else:
                    mat_var = None
                    
                full_mat_name = f"{mat_name} - {mat_var}" if mat_var else mat_name
                
                inv_item = InventoryItem.objects.filter(nama=full_mat_name).first()
                if not inv_item:
                    inv_item = InventoryItem.objects.filter(nama=mat_name).first()
                    if not inv_item:
                        inv_item = InventoryItem.objects.create(
                            nama=full_mat_name,
                            kategori="Bahan Baku",
                            satuan=row.get('uom') or "pcs",
                            stok=0.0
                        )
                
                # Jumlah bahan
                qty_raw = row.get('qty') or '1.0'
                try:
                    qty = float(qty_raw)
                except (ValueError, TypeError):
                    qty = 1.0
                    
                # Buat atau update BoMItem
                bom_item_obj, created = BoMItem.objects.get_or_create(
                    bom=bom_obj,
                    inventory_item=inv_item,
                    defaults={'qty_required_per_unit': qty}
                )
                if not created:
                    bom_item_obj.qty_required_per_unit = qty
                    bom_item_obj.save()
                    
                imported_count += 1
                
        return Response({
            'success': True,
            'message': f'Bahan / Resep berhasil diimpor: {imported_count} baris.'
        }, status=status.HTTP_200_OK)

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

            # BE-22: harga beli stok masuk manual harus memakai rata-rata
            # tertimbang (moving average), BUKAN menimpa harga terakhir, dan
            # berlaku untuk owner apa pun (produk tanpa varian MAUPUN varian).
            # Sebelumnya `and variant is None` membuat varian tak pernah update
            # harga beli-nya, dan produk selalu ditimpa harga terakhir.
            if tipe == 'masuk' and harga_beli is not None:
                if stok_akhir > 0:
                    old_value = stok_awal * (owner.harga_beli or 0)
                    owner.harga_beli = (old_value + qty * harga_beli) / stok_akhir
                else:
                    owner.harga_beli = harga_beli

            owner.save()

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

            # Lapisan biaya FIFO.
            tgl_layer = tanggal or timezone.now().date()
            if tipe == 'masuk':
                stock_fifo.create_layer(
                    product, variant, qty,
                    harga_beli if harga_beli is not None else product.harga_beli,
                    tgl_layer, sumber_tipe='manual', sumber_nomor=f'MV-{movement.id}',
                )
            else:
                stock_fifo.consume_layers(product, variant, qty, movement=movement)

        return Response(ProductStockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='stock-in')
    def stock_in(self, request, pk=None):
        """POST /api/products/{id}/stock-in/ — Stok Masuk (qty, harga_beli opsional, catatan, tanggal)."""
        return self._apply_stock_movement(request, pk, 'masuk')

    @action(detail=True, methods=['get'], url_path='stock-in-history')
    def stock_in_history(self, request, pk=None):
        """GET /api/products/{id}/stock-in-history/ — Lapisan biaya (FIFO) produk ini.

        Membaca StockLayer sungguhan: `sisa_qty` benar-benar sisa yang belum
        terpakai, dan `qty_keluar` dihitung dari lapisan yang sudah dikonsumsi.
        Endpoint ini murni baca — tidak lagi membuat dokumen stok masuk palsu
        seperti implementasi sebelumnya.
        """
        product = self.get_object()
        variant_id = request.query_params.get('variant')

        layers = StockLayer.objects.filter(product=product)
        if variant_id:
            layers = layers.filter(variant_id=variant_id)
        layers = layers.select_related('variant').order_by('-tanggal_masuk', '-id')

        label_sumber = dict(StockLayer.SUMBER_CHOICES)
        data = []
        for l in layers:
            qty = float(l.qty_masuk)
            sisa = float(l.sisa_qty)
            data.append({
                'id': l.id,
                'nomor': l.sumber_nomor or label_sumber.get(l.sumber_tipe, l.sumber_tipe),
                'created_at': l.created_at.isoformat() if l.created_at else None,
                'tanggal': l.tanggal_masuk.isoformat() if l.tanggal_masuk else None,
                'tanggal_kadaluwarsa': l.tanggal_kadaluwarsa.isoformat() if l.tanggal_kadaluwarsa else None,
                'variant_nama': l.variant.nama_varian if l.variant else '',
                'variant_id': l.variant_id,
                'qty': qty,
                'qty_keluar': qty - sisa,
                'sisa_qty': sisa,
                'harga_beli': float(l.harga_beli),
                'rak': l.rak or '',
            })
        return Response(data)

    @action(detail=True, methods=['post'], url_path='update-stock-in-item')
    def update_stock_in_item(self, request, pk=None):
        """POST /api/products/{id}/update-stock-in-item/ — Edit harga_beli, rak, tanggal untuk item stok masuk tertentu."""
        item_id = request.data.get('item_id')
        harga_beli = request.data.get('harga_beli')
        rak = request.data.get('rak')
        tanggal = request.data.get('tanggal')
        
        if not item_id:
            return Response({'error': 'item_id wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
            
        item = get_object_or_404(StockInDocumentItem, id=item_id, product_id=pk)
        doc_nomor = item.document.nomor or f"StockIn-{item.document.id}"
        if doc_nomor.startswith('ADD-PRODUCT'):
            doc_nomor = 'ADD-PRODUCT'

        if harga_beli is not None:
            try:
                old_hb = float(item.harga_beli)
                new_hb = float(_to_decimal(harga_beli, 'harga_beli'))
                if old_hb != new_hb:
                    item.harga_beli = new_hb
                    ProductActivityLog.objects.create(
                        product=item.product,
                        user=request.user,
                        aksi=f"Mengubah harga beli batch {doc_nomor} dari Rp. {old_hb:,.2f} menjadi Rp. {new_hb:,.2f}".replace(",", ".")
                    )
            except ValueError as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if rak is not None:
            old_rak = item.rak or ''
            new_rak = str(rak).strip()
            if old_rak != new_rak:
                item.rak = new_rak
                ProductActivityLog.objects.create(
                    product=item.product,
                    user=request.user,
                    aksi=f"Mengubah rak batch {doc_nomor} menjadi '{new_rak}'"
                )
        item.save()
        
        if tanggal:
            doc = item.document
            old_tgl = str(doc.tanggal)
            if old_tgl != str(tanggal):
                doc.tanggal = tanggal
                doc.save()
                ProductActivityLog.objects.create(
                    product=item.product,
                    user=request.user,
                    aksi=f"Mengubah tanggal batch {doc_nomor} menjadi {tanggal}"
                )
            
        return Response({'success': True})

    @action(detail=True, methods=['get'], url_path='activity-log')
    def activity_log(self, request, pk=None):
        """GET /api/products/{id}/activity-log/ — Ambil log aktivitas untuk produk ini."""
        product = self.get_object()
        logs = ProductActivityLog.objects.filter(product=product).select_related('user').order_by('-created_at')
        data = []
        for log in logs:
            display_user = 'System'
            if log.user:
                display_user = log.user.username
            data.append({
                'id': log.id,
                'tanggal': log.created_at.isoformat() if log.created_at else None,
                'user': display_user,
                'aksi': log.aksi,
                'catatan': log.catatan,
            })
        return Response(data)

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

            # Selaraskan lapisan dengan hasil hitung fisik.
            stock_fifo.recalibrate_layers(
                product, variant, qty_fisik, tanggal or timezone.now().date(),
                sumber_nomor=f'OPN-{movement.id}',
            )

        return Response(ProductStockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)

class ProductImageViewSet(viewsets.ModelViewSet):
    queryset = ProductImage.objects.all().order_by('-is_primary', 'id')
    serializer_class = ProductImageSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        product_id = self.request.query_params.get('product', None)
        if product_id:
            queryset = queryset.filter(product__id=product_id)
        return queryset

class ProductVariantViewSet(viewsets.ModelViewSet):
    queryset = ProductVariant.objects.all().order_by('product__nama', 'nama_varian')
    serializer_class = ProductVariantSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class ProductPackageViewSet(viewsets.ModelViewSet):
    queryset = ProductPackage.objects.all().order_by('nama')
    serializer_class = ProductPackageSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class SpecificationViewSet(viewsets.ModelViewSet):
    queryset = Specification.objects.all().order_by('nama')
    serializer_class = SpecificationSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class ProductSpecValueViewSet(viewsets.ModelViewSet):
    queryset = ProductSpecValue.objects.all().select_related('product', 'specification')
    serializer_class = ProductSpecValueSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class ProductStockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """Riwayat/Pergerakan Stok — dibuat lewat action stock-in/stock-out/stock-opname di ProductViewSet."""
    queryset = ProductStockMovement.objects.all().select_related('product', 'variant', 'user')
    serializer_class = ProductStockMovementSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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
        u = uom.resolve(product, request.data.get('uom_kode'), qty, harga_beli)
        item = StockInDocumentItem.objects.create(
            document=document, product=product,
            harga_beli=u['harga_dasar'] if u['harga_dasar'] is not None else harga_beli,
            qty=u['qty_dasar'], rak=rak,
            tanggal_kadaluwarsa=request.data.get('tanggal_kadaluwarsa') or None,
            uom_kode=u['uom_kode'], uom_konverter=u['uom_konverter'], uom_qty=u['uom_qty'],
        )
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

        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > CSV_IMPORT_MAX_ROWS:
            return Response(
                {'error': f'Maksimal {CSV_IMPORT_MAX_ROWS} baris per import — file ini berisi {len(rows)} baris.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Supplier pada CSV harus sudah terdaftar di master Supplier — mengikuti
        # perilaku Olsera yang menolak import bila supplier belum ada. Kolom
        # supplier boleh KOSONG (template resmi pun mencontohkan baris tanpa
        # supplier); yang ditolak hanya nama yang diisi tapi tidak dikenal.
        # Dicek di depan supaya import ditolak utuh sebelum ada yang dibuat,
        # bukan setengah jalan.
        nama_supplier_csv = {
            _csv_cell(_csv_row_lower(row), 'supplier') for row in rows
        }
        nama_supplier_csv = {nama for nama in nama_supplier_csv if nama}
        if nama_supplier_csv:
            terdaftar = set(
                Supplier.objects
                .annotate(nama_lower=Lower('nama'))
                .filter(nama_lower__in=[n.lower() for n in nama_supplier_csv])
                .values_list('nama_lower', flat=True)
            )
            tidak_dikenal = sorted(
                n for n in nama_supplier_csv if n.lower() not in terdaftar
            )
            if tidak_dikenal:
                return Response(
                    {'error': 'Supplier belum terdaftar: '
                              + ', '.join(f'"{n}"' for n in tidak_dikenal)
                              + '. Tambahkan dulu lewat menu Pelanggan & Supplier, '
                                'atau kosongkan kolom supplier.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        created_items = []
        errors = []
        supplier_name = None

        with transaction.atomic():
            for idx, row in enumerate(rows, start=2):  # baris 1 = header
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
                    errors.append(f"Produk {sku or ''} - {product_name or ''} in row {idx - 2} tidak ditemukan")
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
    @transaction.atomic
    def post_document(self, request, pk=None):
        document = StockInDocument.objects.select_for_update().get(pk=pk)
        if document.status != 'draft':
            return Response({'error': 'Dokumen sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)

        # Catatan/Nama Penerima/Supplier bersifat opsional (sesuai alur Olsera asli);
        # hanya tanggal (wajib sejak dokumen dibuat) dan minimal 1 item yang diperlukan untuk posting.
        if not document.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum posting.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for item in document.items.select_related('product', 'variant'):
                product = Product.objects.select_for_update().get(pk=item.product_id)
                variant = (ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                           if item.variant_id else None)
                owner = variant or product
                stok_awal = owner.qty_stok
                stok_akhir = stok_awal + item.qty
                if item.harga_beli and stok_akhir > 0:
                    old_value = stok_awal * (owner.harga_beli or 0)
                    owner.harga_beli = (old_value + item.qty * item.harga_beli) / stok_akhir
                owner.qty_stok = stok_akhir
                owner.save(update_fields=['qty_stok', 'harga_beli'])

                ProductStockMovement.objects.create(
                    product=product,
                    variant=variant,
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

                stock_fifo.create_layer(
                    product, item.variant, item.qty, item.harga_beli, document.tanggal,
                    sumber_tipe='stock_in', sumber_nomor=document.nomor,
                    rak=item.rak, tanggal_kadaluwarsa=item.tanggal_kadaluwarsa,
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


class PurchaseViewSet(viewsets.ModelViewSet):
    """Pembelian (Purchase Order) + Retur.

    Empat tab layar Pembelian dilayani satu endpoint:
      - Butuh Diproses : status='draft', is_retur=False
      - Telah Diproses : status='selesai', is_retur=False
      - Retur          : is_retur=True
      - Dibatalkan     : status='batal', is_retur=False

    Dua dimensi status independen (Olsera): payment_status (belum/sebagian/lunas)
    dari PurchasePayment, dan receive_status (tunda/diterima). Stok baru bertambah
    saat 'receive' diposting (menghasilkan StockInDocument); retur mengurangi stok
    (menghasilkan StockOutDocument).
    """
    queryset = (
        Purchase.objects
        .all()
        .prefetch_related('items__product', 'items__variant', 'payments')
        .select_related('dibuat_oleh', 'supplier_ref', 'retur_ref')
    )
    serializer_class = PurchaseSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def perform_create(self, serializer):
        today = timezone.now().date()
        nomor = _next_document_number(Purchase, f"PB{today.strftime('%y%m%d')}")
        supplier_ref = self._resolve_supplier(serializer.validated_data.get('supplier'))
        serializer.save(nomor=nomor, dibuat_oleh=self.request.user, supplier_ref=supplier_ref)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa diubah.'}, status=status.HTTP_400_BAD_REQUEST)
        response = super().update(request, *args, **kwargs)
        # Jaga supplier_ref tetap sinkron bila nama supplier diubah.
        instance.refresh_from_db()
        ref = self._resolve_supplier(instance.supplier)
        if ref != instance.supplier_ref:
            instance.supplier_ref = ref
            instance.save(update_fields=['supplier_ref', 'updated_at'])
        return response

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'draft':
            return Response({'error': 'Dokumen yang sudah diposting/dibatalkan tidak bisa dihapus.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @staticmethod
    def _resolve_supplier(nama):
        nama = (nama or '').strip()
        if not nama:
            return None
        return Supplier.objects.filter(nama__iexact=nama).first()

    def _detail_response(self, pk, status_code=status.HTTP_200_OK):
        """Serialisasi ulang dari queryset fresh agar field turunan (total,
        total_dibayar, sisa) tidak basi akibat cache prefetch pada instance yang
        baru saja dimutasi (mis. setelah menambah item/pembayaran)."""
        obj = self.get_queryset().get(pk=pk)
        return Response(PurchaseSerializer(obj).data, status=status_code)

    # ---- kelola item (draft saja) ----
    @action(detail=True, methods=['post'], url_path='add-item')
    def add_item(self, request, pk=None):
        purchase = self.get_object()
        if purchase.status != 'draft':
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
        variant = None
        variant_id = request.data.get('variant')
        if variant_id:
            variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)
        # Satuan alternatif (UOM): qty & harga dikonversi ke satuan dasar.
        u = uom.resolve(product, request.data.get('uom_kode'), qty, harga_beli, variant)
        item = PurchaseItem.objects.create(
            purchase=purchase, product=product, variant=variant,
            harga_beli=u['harga_dasar'] if u['harga_dasar'] is not None else harga_beli,
            qty=u['qty_dasar'],
            tanggal_kadaluwarsa=request.data.get('tanggal_kadaluwarsa') or None,
            uom_kode=u['uom_kode'], uom_konverter=u['uom_konverter'], uom_qty=u['uom_qty'],
        )
        return Response(PurchaseItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='remove-item')
    def remove_item(self, request, pk=None):
        purchase = self.get_object()
        if purchase.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)
        item_id = request.data.get('item_id')
        deleted, _ = PurchaseItem.objects.filter(purchase=purchase, id=item_id).delete()
        if not deleted:
            return Response({'error': 'Item tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        return self._detail_response(purchase.pk)

    @action(detail=True, methods=['post'], url_path='import-csv')
    def import_csv(self, request, pk=None):
        """Import massal item dari CSV. Kolom: product, variant, sku, supplier, qty, new_buy_price."""
        purchase = self.get_object()
        if purchase.status != 'draft':
            return Response({'error': 'Dokumen tidak dalam status draft.'}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > CSV_IMPORT_MAX_ROWS:
            return Response(
                {'error': f'Maksimal {CSV_IMPORT_MAX_ROWS} baris per import — file ini berisi {len(rows)} baris.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Supplier pada CSV harus sudah terdaftar (mengikuti Olsera); kolom boleh kosong.
        nama_supplier_csv = {_csv_cell(_csv_row_lower(row), 'supplier') for row in rows}
        nama_supplier_csv = {nama for nama in nama_supplier_csv if nama}
        if nama_supplier_csv:
            terdaftar = set(
                Supplier.objects.annotate(nama_lower=Lower('nama'))
                .filter(nama_lower__in=[n.lower() for n in nama_supplier_csv])
                .values_list('nama_lower', flat=True)
            )
            tidak_dikenal = sorted(n for n in nama_supplier_csv if n.lower() not in terdaftar)
            if tidak_dikenal:
                return Response(
                    {'error': 'Supplier belum terdaftar: ' + ', '.join(f'"{n}"' for n in tidak_dikenal)
                              + '. Tambahkan dulu lewat menu Pelanggan & Supplier, atau kosongkan kolom supplier.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        created_items = []
        errors = []
        supplier_name = None

        with transaction.atomic():
            for idx, row in enumerate(rows, start=2):
                row_lower = _csv_row_lower(row)
                product_name = _csv_cell(row_lower, 'product')
                variant_name = _csv_cell(row_lower, 'variant')
                sku = _csv_cell(row_lower, 'sku')
                supplier = _csv_cell(row_lower, 'supplier')
                qty_raw = _csv_cell(row_lower, 'qty')
                harga_raw = _csv_cell(row_lower, 'new_buy_price', 'new buy price', 'price')

                if supplier and not supplier_name:
                    supplier_name = supplier

                product = None
                if sku:
                    product = Product.objects.filter(sku=sku).first()
                if not product and product_name:
                    product = Product.objects.filter(nama__iexact=product_name).first()
                if not product:
                    errors.append(f"Produk {sku or ''} - {product_name or ''} di baris {idx} tidak ditemukan")
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
                        errors.append(f"Baris {idx}: harga beli '{harga_raw}' tidak valid, memakai harga beli produk.")

                variant = None
                if variant_name:
                    variant = ProductVariant.objects.filter(product=product, nama_varian__iexact=variant_name).first()

                item = PurchaseItem.objects.create(purchase=purchase, product=product, variant=variant, harga_beli=harga_beli, qty=qty)
                created_items.append(item)

            if supplier_name and not purchase.supplier:
                purchase.supplier = supplier_name
                purchase.supplier_ref = self._resolve_supplier(supplier_name)
                purchase.save(update_fields=['supplier', 'supplier_ref', 'updated_at'])

        return Response(
            {
                'document': PurchaseSerializer(self.get_queryset().get(pk=purchase.pk)).data,
                'created': PurchaseItemSerializer(created_items, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST,
        )

    # ---- pembayaran (cicilan/DP) ----
    @action(detail=True, methods=['post'], url_path='add-payment')
    def add_payment(self, request, pk=None):
        purchase = self.get_object()
        if purchase.is_retur:
            return Response({'error': 'Dokumen retur tidak menerima pembayaran.'}, status=status.HTTP_400_BAD_REQUEST)
        if purchase.status == 'batal':
            return Response({'error': 'Dokumen batal tidak bisa menerima pembayaran.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            nominal = _to_decimal(request.data.get('nominal'), 'nominal')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if nominal <= 0:
            return Response({'error': 'Nominal pembayaran harus lebih besar dari 0.'}, status=status.HTTP_400_BAD_REQUEST)

        tanggal = request.data.get('tanggal') or timezone.now().date()
        PurchasePayment.objects.create(
            purchase=purchase,
            tanggal=tanggal,
            nominal=nominal,
            metode=(request.data.get('metode') or '').strip(),
            catatan=(request.data.get('catatan') or '').strip(),
            dibuat_oleh=request.user,
        )
        purchase.recompute_payment_status()
        return self._detail_response(purchase.pk, status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='remove-payment')
    def remove_payment(self, request, pk=None):
        purchase = self.get_object()
        payment_id = request.data.get('payment_id')
        deleted, _ = PurchasePayment.objects.filter(purchase=purchase, id=payment_id).delete()
        if not deleted:
            return Response({'error': 'Pembayaran tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        purchase.recompute_payment_status()
        return self._detail_response(purchase.pk)

    # ---- penerimaan barang (menambah stok) ----
    @action(detail=True, methods=['post'], url_path='receive')
    def receive(self, request, pk=None):
        purchase = self.get_object()
        if purchase.is_retur:
            return Response({'error': 'Gunakan post-retur untuk dokumen retur.'}, status=status.HTTP_400_BAD_REQUEST)
        if purchase.status != 'draft':
            return Response({'error': 'Dokumen sudah diproses/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        if not purchase.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum menerima.'}, status=status.HTTP_400_BAD_REQUEST)

        lanjut_raw = request.data.get('lanjut_tambah_stok')
        lanjut_tambah_stok = purchase.lanjut_tambah_stok if lanjut_raw is None else _parse_bool_flag(lanjut_raw)

        with transaction.atomic():
            purchase.tanggal_diterima = request.data.get('tanggal_diterima') or timezone.now().date()
            purchase.no_terima = (request.data.get('no_terima') or '').strip()
            purchase.lanjut_tambah_stok = lanjut_tambah_stok
            if lanjut_tambah_stok:
                err = self._apply_purchase_stock(purchase, request, direction='in')
                if err:
                    return err
            purchase.receive_status = 'diterima'
            purchase.status = 'selesai'
            purchase.save()
        return self._detail_response(purchase.pk)

    # ---- retur ----
    @action(detail=True, methods=['post'], url_path='create-retur')
    def create_retur(self, request, pk=None):
        """Buat draft retur dari PO ini (pk = PO asal). Syarat: Diterima + Lunas."""
        source = self.get_object()
        if source.is_retur:
            return Response({'error': 'Tidak bisa meretur dokumen retur.'}, status=status.HTTP_400_BAD_REQUEST)
        if source.receive_status != 'diterima' or source.payment_status != 'lunas':
            return Response({'error': 'Retur hanya untuk pembelian yang sudah Diterima dan Lunas.'}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.now().date()
        with transaction.atomic():
            retur = Purchase.objects.create(
                nomor=_next_document_number(Purchase, f"RB{today.strftime('%y%m%d')}"),
                tanggal=request.data.get('tanggal') or today,
                supplier=source.supplier,
                supplier_ref=source.supplier_ref,
                mata_uang=source.mata_uang,
                catatan=(request.data.get('catatan') or '').strip(),
                is_retur=True,
                retur_ref=source,
                status='draft',
                dibuat_oleh=request.user,
            )
            for it in source.items.all():
                PurchaseItem.objects.create(purchase=retur, product=it.product, variant=it.variant, qty=it.qty, harga_beli=it.harga_beli)
        return self._detail_response(retur.pk, status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='post-retur')
    def post_retur(self, request, pk=None):
        retur = self.get_object()
        if not retur.is_retur:
            return Response({'error': 'Bukan dokumen retur.'}, status=status.HTTP_400_BAD_REQUEST)
        if retur.status != 'draft':
            return Response({'error': 'Retur sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        if not retur.items.exists():
            return Response({'error': 'Tidak ada produk untuk diretur.'}, status=status.HTTP_400_BAD_REQUEST)

        exchange = _parse_bool_flag(request.data.get('exchange_new'))
        with transaction.atomic():
            err = self._apply_purchase_stock(retur, request, direction='out')
            if err:
                return err
            if exchange:
                # Supplier menukar barang rusak dengan barang baru -> stok ditambah kembali.
                retur.exchange_new = True
                err2 = self._apply_purchase_stock(retur, request, direction='in')
                if err2:
                    return err2
            retur.status = 'selesai'
            retur.receive_status = 'diterima'
            retur.save()
        return self._detail_response(retur.pk)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        purchase = self.get_object()
        if purchase.status != 'draft':
            return Response({'error': 'Hanya dokumen draft yang bisa dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)
        purchase.status = 'batal'
        purchase.save()
        return self._detail_response(purchase.pk)

    # ---- helper stok ----
    def _apply_purchase_stock(self, purchase, request, direction):
        """direction='in' menambah stok (buat StockInDocument), 'out' mengurangi
        stok (buat StockOutDocument). Return Response error, atau None bila sukses."""
        today = timezone.now().date()
        tanggal = purchase.tanggal_diterima or purchase.tanggal or today

        if direction == 'in':
            doc = StockInDocument.objects.create(
                nomor=_next_document_number(StockInDocument, f"IN{today.strftime('%y%m%d')}"),
                tanggal=tanggal, catatan=purchase.catatan, supplier=purchase.supplier,
                status='selesai', dibuat_oleh=request.user, purchase=purchase,
            )
            for it in purchase.items.select_related('product', 'variant'):
                if it.variant_id:
                    owner = ProductVariant.objects.select_for_update().get(pk=it.variant_id)
                else:
                    owner = Product.objects.select_for_update().get(pk=it.product_id)
                stok_awal = owner.qty_stok
                stok_akhir = stok_awal + it.qty
                owner.qty_stok = stok_akhir
                owner.save()
                if it.harga_beli:
                    Product.objects.filter(pk=it.product_id).update(harga_beli=it.harga_beli)
                ProductStockMovement.objects.create(
                    product=it.product, variant=it.variant, user=request.user, tipe='masuk',
                    qty=it.qty, harga_beli=it.harga_beli, stok_awal=stok_awal, stok_akhir=stok_akhir,
                    catatan=purchase.catatan, tanggal=tanggal, stock_in_document=doc,
                )
                stock_fifo.create_layer(
                    it.product, it.variant, it.qty, it.harga_beli, tanggal,
                    sumber_tipe='purchase', sumber_nomor=purchase.nomor,
                    tanggal_kadaluwarsa=it.tanggal_kadaluwarsa,
                )
            return None

        # direction == 'out'
        doc = StockOutDocument.objects.create(
            nomor=_next_document_number(StockOutDocument, f"OUT{today.strftime('%y%m%d')}"),
            tanggal=tanggal, catatan=purchase.catatan, alasan='refund',
            status='selesai', dibuat_oleh=request.user, purchase=purchase,
        )
        for it in purchase.items.select_related('product', 'variant'):
            if it.variant_id:
                owner = ProductVariant.objects.select_for_update().get(pk=it.variant_id)
            else:
                owner = Product.objects.select_for_update().get(pk=it.product_id)
            stok_awal = owner.qty_stok
            stok_akhir = stok_awal - it.qty
            if stok_akhir < 0:
                return Response(
                    {'error': f"Stok '{owner}' tidak cukup untuk retur. Stok saat ini {stok_awal}, diminta {it.qty}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            owner.qty_stok = stok_akhir
            owner.save()
            mv = ProductStockMovement.objects.create(
                product=it.product, variant=it.variant, user=request.user, tipe='keluar',
                qty=it.qty, stok_awal=stok_awal, stok_akhir=stok_akhir,
                catatan=purchase.catatan, tanggal=tanggal, stock_out_document=doc,
            )
            stock_fifo.consume_layers(it.product, it.variant, it.qty, movement=mv)
        return None


class StockOutDocumentViewSet(viewsets.ModelViewSet):
    """Dokumen Stok Keluar: header + banyak item, status draft -> selesai/batal."""
    queryset = StockOutDocument.objects.all().prefetch_related('items__product').select_related('dibuat_oleh')
    serializer_class = StockOutDocumentSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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

        u = uom.resolve(product, request.data.get('uom_kode'), qty, None, variant)
        item = StockOutDocumentItem.objects.create(
            document=document, product=product, variant=variant, qty=u['qty_dasar'],
            uom_kode=u['uom_kode'], uom_konverter=u['uom_konverter'], uom_qty=u['uom_qty'],
        )
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

        # Olsera membatasi import Stok Keluar di 500 baris (Stok Masuk 200).
        # Sebelumnya di sini tidak ada batas sama sekali: seluruh file diproses
        # dalam satu transaction.atomic() tanpa plafon.
        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > CSV_IMPORT_MAX_ROWS_STOCK_OUT:
            return Response(
                {'error': f'Maksimal {CSV_IMPORT_MAX_ROWS_STOCK_OUT} baris per import — file ini berisi {len(rows)} baris.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_items = []
        errors = []
        to_store_url_id = None

        with transaction.atomic():
            for idx, row in enumerate(rows, start=2):  # baris 1 = header
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
    @transaction.atomic
    def post_document(self, request, pk=None):
        document = StockOutDocument.objects.select_for_update().get(pk=pk)
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
                    raise ValidationError({'error': f"Stok produk '{owner}' tidak mencukupi. Stok saat ini {stok_awal}, diminta {item.qty}."})

                owner.qty_stok = stok_akhir
                owner.save()

                mv = ProductStockMovement.objects.create(
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
                stock_fifo.consume_layers(item.product, item.variant, item.qty, movement=mv)

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
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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
    @transaction.atomic
    def post_document(self, request, pk=None):
        document = StockProductionDocument.objects.select_for_update().get(pk=pk)
        if document.status != 'draft':
            return Response({'error': 'Dokumen sudah diposting/dibatalkan.'}, status=status.HTTP_400_BAD_REQUEST)

        if not document.items.exists():
            return Response({'error': 'Tambahkan minimal satu produk sebelum posting.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            items = list(document.items.select_related('product', 'variant'))
            # Dihitung sekali di luar loop: alokasi butuh melihat seluruh item
            # untuk menentukan bobot proporsionalnya.
            biaya_unit = production_costing.biaya_per_unit(document, items)

            for item in items:
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
                # Barang jadi masuk sebagai lapisan baru senilai harga beli
                # produk, ditambah porsi biaya produksi bila dokumen ini
                # mengaktifkan penyerapan ke HPP (default: tidak).
                harga_layer = item.product.harga_beli + biaya_unit.get(item.id, 0)
                stock_fifo.create_layer(
                    item.product, item.variant, item.qty, harga_layer,
                    document.tanggal, sumber_tipe='produksi', sumber_nomor=document.nomor,
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
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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

                # Rak melekat di produk/varian (Produk > Lacak Inventori), bukan
                # diketik per item opname — jadi diwarisi dari 'owner' seperti
                # halnya stok_sistem. Tanpa ini kolom Rack di layar opname selalu
                # '-' walau rak produknya sudah diisi.
                item = StockOpnameDocumentItem.objects.create(
                    document=document, product=product, variant=variant,
                    jam_opname=jam_opname, rak=owner.rack,
                    stok_sistem=owner.qty_stok, stok_aktual=0,
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
    @transaction.atomic
    def post_document(self, request, pk=None):
        document = StockOpnameDocument.objects.select_for_update().get(pk=pk)
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
                stock_fifo.recalibrate_layers(
                    group['product'], group['variant'], stok_akhir, document.tanggal,
                    sumber_nomor=document.nomor,
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


class StockFifoSyncView(APIView):
    """POST /api/stock-fifo/sync/ — tombol "Sync Stok Produk".

    Membuat satu lapisan saldo awal untuk produk/varian yang punya stok tapi
    belum punya lapisan. Idempoten.
    """
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def post(self, request):
        with transaction.atomic():
            dibuat = stock_fifo.sync_opening_layers()
        rekon = stock_fifo.reconciliation_report()
        return Response({
            'lapisan_dibuat': dibuat,
            'mode': stock_fifo.get_stock_system(),
            'tanpa_lapisan': rekon['tanpa_lapisan'],
            'tidak_cocok': len(rekon['tidak_cocok']),
        })


class StockFifoStatusView(APIView):
    """GET /api/stock-fifo/status/ — kesehatan lapisan FIFO."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rekon = stock_fifo.reconciliation_report()
        return Response({
            'mode': stock_fifo.get_stock_system(),
            'total_lapisan': StockLayer.objects.count(),
            'lapisan_terbuka': StockLayer.objects.filter(sisa_qty__gt=0).count(),
            'tanpa_lapisan': rekon['tanpa_lapisan'],
            'tidak_cocok': rekon['tidak_cocok'][:50],
            'jumlah_tidak_cocok': len(rekon['tidak_cocok']),
        })
