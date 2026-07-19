from rest_framework import serializers

from .finance_models import CashTransactionType, CashTransaction, CashTransactionAttachment


class CashTransactionTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashTransactionType
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']


class CashTransactionAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    nama = serializers.SerializerMethodField()

    class Meta:
        model = CashTransactionAttachment
        fields = ['id', 'file', 'file_url', 'nama', 'created_at']

    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url

    def get_nama(self, obj):
        return obj.file.name.split('/')[-1] if obj.file else None


class CashTransactionSerializer(serializers.ModelSerializer):
    lampiran = CashTransactionAttachmentSerializer(many=True, read_only=True)
    tipe_transaksi_nama = serializers.ReadOnlyField(source='tipe_transaksi.nama')
    staff_nama = serializers.ReadOnlyField(source='staff.username')
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = CashTransaction
        fields = '__all__'
        read_only_fields = ['nomor', 'arah', 'dibuat_oleh']
