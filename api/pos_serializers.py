from rest_framework import serializers
from .pos_models import POSSale, POSSaleItem

class POSSaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSSaleItem
        fields = '__all__'

class POSSaleSerializer(serializers.ModelSerializer):
    items = POSSaleItemSerializer(many=True, read_only=True)
    kasir_name = serializers.ReadOnlyField(source='kasir.username')
    pelanggan_name = serializers.ReadOnlyField(source='pelanggan.nama')

    class Meta:
        model = POSSale
        fields = '__all__'
