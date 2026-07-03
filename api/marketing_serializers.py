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
