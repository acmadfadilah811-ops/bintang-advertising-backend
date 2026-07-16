from rest_framework import serializers
from .marketing_models import SalesDiscount, DiscountCoupon, POSPromotion


class SalesDiscountSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = SalesDiscount
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']


class DiscountCouponSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = DiscountCoupon
        fields = '__all__'
        read_only_fields = ['dibuat_oleh', 'penggunaan_count']


class POSPromotionSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = POSPromotion
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']

    def validate_produk_qty(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("produk_qty harus berupa list.")
        
        validated_items = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f"Item ke-{index} harus berupa dictionary.")
            
            if 'nama' not in item or not isinstance(item['nama'], str) or not item['nama'].strip():
                raise serializers.ValidationError(f"Item ke-{index} wajib memiliki field 'nama' berupa string.")
            
            qty = item.get('qty', 1)
            try:
                qty = int(qty)
            except (ValueError, TypeError):
                raise serializers.ValidationError(f"Item ke-{index} memiliki 'qty' yang tidak valid.")
            
            if qty < 1:
                raise serializers.ValidationError(f"Item ke-{index} memiliki 'qty' kurang dari 1.")
            
            validated_items.append({
                "nama": item['nama'].strip(),
                "qty": qty
            })
        return validated_items

