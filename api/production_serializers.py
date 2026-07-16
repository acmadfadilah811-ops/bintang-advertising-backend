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
    # SerializerMethodField, bukan ReadOnlyField(source='akun.kode_akun'):
    # kalau akun kosong (boleh, karena opsional), ReadOnlyField dengan source
    # bertitik MENGHILANGKAN key-nya dari payload alih-alih mengirim null —
    # frontend jadi menerima undefined. Ada test untuk ini.
    akun_kode = serializers.SerializerMethodField()
    akun_nama = serializers.SerializerMethodField()

    class Meta:
        model = ProductionCost
        fields = ['id', 'nama', 'nilai', 'akun', 'akun_kode', 'akun_nama']

    def get_akun_kode(self, obj):
        return obj.akun.kode_akun if obj.akun else None

    def get_akun_nama(self, obj):
        return obj.akun.nama_akun if obj.akun else None


class StockProductionDocumentCostSerializer(serializers.ModelSerializer):
    production_cost_nama = serializers.ReadOnlyField(source='production_cost.nama')
    # Sama seperti di ProductionCostSerializer: akun opsional, jadi key-nya
    # harus tetap ada bernilai null — bukan hilang dari payload.
    akun_kode = serializers.SerializerMethodField()
    akun_nama = serializers.SerializerMethodField()

    class Meta:
        model = StockProductionDocumentCost
        fields = [
            'id', 'document', 'production_cost', 'production_cost_nama',
            'akun_kode', 'akun_nama', 'nilai',
        ]

    def get_akun_kode(self, obj):
        akun = obj.production_cost.akun
        return akun.kode_akun if akun else None

    def get_akun_nama(self, obj):
        akun = obj.production_cost.akun
        return akun.nama_akun if akun else None

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
