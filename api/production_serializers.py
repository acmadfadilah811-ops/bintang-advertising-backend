"""Serializer biaya produksi.

File terpisah — product_serializers.py sudah 851 baris, jauh di atas batas.
"""

from rest_framework import serializers

from .production_models import ProductionCost, StockProductionDocumentCost


class ProductionCostSerializer(serializers.ModelSerializer):
    # Ditampilkan supaya frontend TIDAK perlu memanggil /api/finance/akun/
    # hanya untuk menampilkan nama akun. Endpoint akun dibatasi
    # IsOwnerOrManager (hr/views.py), jadi staff/kasir akan kena 403 kalau
    # halaman ini memanggilnya saat dimuat.
    akun_kode = serializers.ReadOnlyField(source='akun.kode_akun')
    akun_nama = serializers.ReadOnlyField(source='akun.nama_akun')

    class Meta:
        model = ProductionCost
        fields = ['id', 'nama', 'nilai', 'akun', 'akun_kode', 'akun_nama']


class StockProductionDocumentCostSerializer(serializers.ModelSerializer):
    production_cost_nama = serializers.ReadOnlyField(source='production_cost.nama')
    akun_kode = serializers.ReadOnlyField(source='production_cost.akun.kode_akun')
    akun_nama = serializers.ReadOnlyField(source='production_cost.akun.nama_akun')

    class Meta:
        model = StockProductionDocumentCost
        fields = [
            'id', 'document', 'production_cost', 'production_cost_nama',
            'akun_kode', 'akun_nama', 'nilai',
        ]

    def validate(self, attrs):
        # Dokumen yang sudah diposting/dibatalkan tidak boleh diutak-atik —
        # aturan yang sama dengan add_item di StockProductionDocumentViewSet.
        document = attrs.get('document') or getattr(self.instance, 'document', None)
        if document and document.status != 'draft':
            raise serializers.ValidationError(
                {'error': 'Dokumen tidak dalam status draft.'}
            )
        nilai = attrs.get('nilai')
        if nilai is not None and nilai < 0:
            raise serializers.ValidationError({'error': 'nilai tidak boleh negatif.'})
        return attrs
