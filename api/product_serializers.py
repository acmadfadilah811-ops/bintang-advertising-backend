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
    class Meta:
        model = ProductCategory
        fields = '__all__'

class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = '__all__'

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

    class Meta:
        model = Product
        fields = '__all__'

class ProductPackageItemSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama')
    
    class Meta:
        model = ProductPackageItem
        fields = '__all__'

class ProductPackageSerializer(serializers.ModelSerializer):
    items = ProductPackageItemSerializer(many=True, read_only=True)
    
    class Meta:
        model = ProductPackage
        fields = '__all__'

class AddonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Addon
        fields = '__all__'

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

    class Meta:
        model = StockInDocumentItem
        fields = '__all__'

class StockInDocumentSerializer(serializers.ModelSerializer):
    items = StockInDocumentItemSerializer(many=True, read_only=True)
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = StockInDocument
        fields = '__all__'
        read_only_fields = ['nomor', 'status', 'dibuat_oleh']


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

    class Meta:
        model = StockProductionDocument
        fields = '__all__'
        read_only_fields = ['nomor', 'status', 'dibuat_oleh']


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
