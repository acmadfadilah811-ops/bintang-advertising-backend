from decimal import Decimal

from rest_framework import serializers
from .product_models import (
    ProductCategory, Brand, SpecialType, Collection,
    Product, ProductVariant, ProductPackage, ProductPackageItem,
    Addon, Specification, ProductSpecValue, ProductImage, ProductStockMovement,
    StockInDocument, StockInDocumentItem, StockOutDocument, StockOutDocumentItem,
    StockProductionDocument, StockProductionDocumentItem,
    StockOpnameDocument, StockOpnameDocumentItem
)

class ProductCategorySerializer(serializers.ModelSerializer):
    products_count = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = '__all__'

    def get_products_count(self, obj):
        return obj.products.count()

class BrandSerializer(serializers.ModelSerializer):
    products_count = serializers.SerializerMethodField()

    class Meta:
        model = Brand
        fields = '__all__'

    def get_products_count(self, obj):
        return obj.products.count()

class SpecialTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpecialType
        fields = '__all__'

class CollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collection
        fields = '__all__'

class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = '__all__'

class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = '__all__'

class SpecificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Specification
        fields = '__all__'

class ProductSpecValueSerializer(serializers.ModelSerializer):
    specification_nama = serializers.ReadOnlyField(source='specification.nama')
    
    class Meta:
        model = ProductSpecValue
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    fotos = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    specifications = ProductSpecValueSerializer(many=True, read_only=True)
    kategori_nama = serializers.ReadOnlyField(source='kategori.nama')
    brand_nama = serializers.ReadOnlyField(source='brand.nama')
    koleksi_nama = serializers.ReadOnlyField(source='koleksi.nama')
    related_products_details = serializers.SerializerMethodField()

    def get_related_products_details(self, obj):
        ids = obj.related_product_ids
        if not ids or not isinstance(ids, list):
            return []
        # Gunakan filter is_active=True untuk memastikan produk yang dihapus tidak muncul
        related_prods = Product.objects.filter(id__in=ids, is_active=True)
        details = []
        for p in related_prods:
            foto_url = None
            if p.fotos.exists():
                foto_url = p.fotos.first().foto.url
            details.append({
                "id": p.id,
                "nama": p.nama,
                "sku": p.sku,
                "barcode": p.barcode,
                "harga_jual_toko": float(p.harga_jual_toko),
                "has_variant": p.has_variant,
                "foto_url": foto_url
            })
        return details

    class Meta:
        model = Product
        fields = '__all__'

class ProductPackageItemSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    product_sku = serializers.ReadOnlyField(source='product.sku')
    product_foto = serializers.SerializerMethodField()
    product_varian_nama = serializers.SerializerMethodField()
    
    class Meta:
        model = ProductPackageItem
        fields = '__all__'

    def get_product_foto(self, obj):
        first_img = obj.product.fotos.filter(is_primary=True).first() or obj.product.fotos.first()
        if first_img and first_img.foto:
            return first_img.foto.url
        return None

    def get_product_varian_nama(self, obj):
        if obj.variant:
            return obj.variant.nama_varian
        first_variant = obj.product.variants.first()
        if first_variant:
            return first_variant.nama_varian
        return None

class ProductPackageSerializer(serializers.ModelSerializer):
    items = ProductPackageItemSerializer(many=True, read_only=True)
    
    class Meta:
        model = ProductPackage
        fields = '__all__'

    def create(self, validated_data):
        request = self.context.get('request')
        items_data = []
        if request and 'items' in request.data:
            raw_items = request.data.get('items')
            if isinstance(raw_items, str):
                import json
                try:
                    items_data = json.loads(raw_items)
                except Exception:
                    items_data = []
            else:
                items_data = raw_items
        
        package = ProductPackage.objects.create(**validated_data)
        for item in items_data:
            product_id = item.get('product') or item.get('product_id')
            variant_id = item.get('variant') or item.get('variant_id')
            qty = item.get('qty', 1)
            if product_id:
                ProductPackageItem.objects.create(paket=package, product_id=product_id, variant_id=variant_id, qty=qty)
        return package

    def update(self, instance, validated_data):
        request = self.context.get('request')
        items_data = None
        if request and 'items' in request.data:
            raw_items = request.data.get('items')
            if isinstance(raw_items, str):
                import json
                try:
                    items_data = json.loads(raw_items)
                except Exception:
                    items_data = []
            else:
                items_data = raw_items
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                product_id = item.get('product') or item.get('product_id')
                variant_id = item.get('variant') or item.get('variant_id') or item.get('variant_id')
                qty = item.get('qty', 1)
                if product_id:
                    ProductPackageItem.objects.create(paket=instance, product_id=product_id, variant_id=variant_id, qty=qty)
        return instance

class AddonSerializer(serializers.ModelSerializer):
    linked_product_nama = serializers.ReadOnlyField(source='linked_product.nama')
    linked_variant_nama = serializers.ReadOnlyField(source='linked_variant.nama_varian')
    applies_to_names = serializers.SerializerMethodField()
    applies_to_category_names = serializers.SerializerMethodField()

    class Meta:
        model = Addon
        fields = '__all__'

    def get_applies_to_names(self, obj):
        return [p.nama for p in obj.applies_to.all()]

    def get_applies_to_category_names(self, obj):
        return [c.nama for c in obj.applies_to_categories.all()]

class ProductStockMovementSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    variant_nama = serializers.ReadOnlyField(source='variant.nama_varian')
    user_nama = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = ProductStockMovement
        fields = '__all__'
        read_only_fields = ['stok_awal', 'stok_akhir', 'user']

class StockInDocumentItemSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    product_sku = serializers.ReadOnlyField(source='product.sku')
    product_satuan = serializers.ReadOnlyField(source='product.satuan')
    variant_nama = serializers.ReadOnlyField(source='variant.nama_varian')

    class Meta:
        model = StockInDocumentItem
        fields = '__all__'

class StockInDocumentSerializer(serializers.ModelSerializer):
    items = StockInDocumentItemSerializer(many=True, read_only=True)
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')
    # Email pembuat, untuk kartu Log di layar detail pembelian. Method field agar
    # tetap mengirim null (bukan membuang field) saat akun pembuat sudah dihapus.
    dibuat_oleh_email = serializers.SerializerMethodField()

    class Meta:
        model = StockInDocument
        fields = '__all__'
        read_only_fields = ['nomor', 'status', 'dibuat_oleh']

    def get_dibuat_oleh_email(self, obj):
        return obj.dibuat_oleh.email if obj.dibuat_oleh else None


class StockOutDocumentItemSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    product_sku = serializers.ReadOnlyField(source='product.sku')
    product_satuan = serializers.ReadOnlyField(source='product.satuan')
    variant_nama = serializers.ReadOnlyField(source='variant.nama_varian')

    class Meta:
        model = StockOutDocumentItem
        fields = '__all__'


class StockOutDocumentSerializer(serializers.ModelSerializer):
    items = StockOutDocumentItemSerializer(many=True, read_only=True)
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = StockOutDocument
        fields = '__all__'
        read_only_fields = ['nomor', 'status', 'dibuat_oleh']

    def validate(self, attrs):
        """'Alasan lainnya' tanpa teks tidak menjelaskan apa pun — ditolak.
        Sebaliknya, teks bebas sisa pilihan sebelumnya dibuang, supaya tidak ada
        dokumen ber-alasan 'Rusak' yang diam-diam masih menyimpan keterangan lama."""
        # PATCH parsial: pakai nilai tersimpan bila field-nya tidak ikut dikirim.
        alasan = attrs.get('alasan', getattr(self.instance, 'alasan', None))
        if 'alasan_lainnya' in attrs:
            teks = (attrs.get('alasan_lainnya') or '').strip()
        else:
            teks = (getattr(self.instance, 'alasan_lainnya', '') or '').strip()

        if alasan == 'lainnya':
            if not teks:
                raise serializers.ValidationError(
                    {'alasan_lainnya': 'Wajib diisi saat memilih "Alasan lainnya".'}
                )
            attrs['alasan_lainnya'] = teks
        elif alasan is not None:
            attrs['alasan_lainnya'] = ''
        return attrs


class StockProductionDocumentItemSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    product_sku = serializers.ReadOnlyField(source='product.sku')
    product_satuan = serializers.ReadOnlyField(source='product.satuan')
    variant_nama = serializers.ReadOnlyField(source='variant.nama_varian')

    class Meta:
        model = StockProductionDocumentItem
        fields = '__all__'


class StockProductionDocumentSerializer(serializers.ModelSerializer):
    items = StockProductionDocumentItemSerializer(many=True, read_only=True)
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')
    biaya = serializers.SerializerMethodField()
    total_biaya = serializers.SerializerMethodField()

    class Meta:
        model = StockProductionDocument
        fields = '__all__'
        read_only_fields = ['nomor', 'status', 'dibuat_oleh']

    def get_biaya(self, obj):
        # Diimpor di dalam method untuk menghindari circular import
        # (production_serializers -> production_models -> api.models).
        from .production_serializers import StockProductionDocumentCostSerializer
        return StockProductionDocumentCostSerializer(obj.biaya.all(), many=True).data

    def get_total_biaya(self, obj):
        # Jumlah dari BIAYA dokumen — bukan dari item. Item adalah produk &
        # qty, bukan uang.
        return sum((b.nilai for b in obj.biaya.all()), Decimal('0'))


class StockOpnameDocumentItemSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    product_sku = serializers.ReadOnlyField(source='product.sku')
    product_satuan = serializers.ReadOnlyField(source='product.satuan')
    product_harga_beli = serializers.ReadOnlyField(source='product.harga_beli')
    product_harga_jual_toko = serializers.ReadOnlyField(source='product.harga_jual_toko')
    variant_nama = serializers.ReadOnlyField(source='variant.nama_varian')
    selisih = serializers.SerializerMethodField()
    selisih_harga = serializers.SerializerMethodField()
    selisih_harga_jual = serializers.SerializerMethodField()

    class Meta:
        model = StockOpnameDocumentItem
        fields = '__all__'
        read_only_fields = ['stok_sistem']

    def get_selisih(self, obj):
        return obj.stok_aktual - obj.stok_sistem

    def get_selisih_harga(self, obj):
        return (obj.stok_aktual - obj.stok_sistem) * obj.product.harga_beli

    def get_selisih_harga_jual(self, obj):
        return (obj.stok_aktual - obj.stok_sistem) * obj.product.harga_jual_toko


class StockOpnameDocumentSerializer(serializers.ModelSerializer):
    items = StockOpnameDocumentItemSerializer(many=True, read_only=True)
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = StockOpnameDocument
        fields = '__all__'
        read_only_fields = ['nomor', 'status', 'dibuat_oleh']
