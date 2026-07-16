"""ViewSet biaya produksi.

Sengaja di file sendiri, bukan menambah ke product_views.py yang sudah
2.100+ baris (batas AGENTS.md: 400).

Biaya per-dokumen dibuat sebagai resource tersendiri
(/api/stock-production-costs/) alih-alih action di StockProductionDocumentViewSet,
supaya file god itu tidak bertambah gemuk.
"""

from rest_framework import status, viewsets
from rest_framework.response import Response

from api.permissions import IsOwnerManagerAdminOrReadOnly

from .production_models import ProductionCost, StockProductionDocumentCost
from .production_serializers import (
    ProductionCostSerializer,
    StockProductionDocumentCostSerializer,
)


class ProductionCostViewSet(viewsets.ModelViewSet):
    """Master komponen biaya produksi: GET /api/production-costs/"""

    queryset = ProductionCost.objects.select_related('akun').all()
    serializer_class = ProductionCostSerializer
    # Biaya produksi menyangkut uang: staff/kasir boleh baca (untuk melihat
    # rincian dokumen), hanya manajemen yang boleh mengubah.
    permission_classes = [IsOwnerManagerAdminOrReadOnly]


class StockProductionDocumentCostViewSet(viewsets.ModelViewSet):
    """Biaya yang dibebankan ke sebuah dokumen produksi.

    Penjagaan draft-only ada di StockProductionDocumentCostSerializer.validate().
    """

    queryset = StockProductionDocumentCost.objects.select_related(
        'production_cost__akun', 'document'
    ).all()
    serializer_class = StockProductionDocumentCostSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        document_id = self.request.query_params.get('document')
        if document_id:
            qs = qs.filter(document_id=document_id)
        return qs

    def destroy(self, request, *args, **kwargs):
        # validate() pada serializer TIDAK dipanggil saat DELETE, jadi
        # penjagaan draft-only harus diulang di sini. Tanpa ini, biaya pada
        # dokumen yang sudah diposting masih bisa dihapus.
        instance = self.get_object()
        if instance.document.status != 'draft':
            return Response(
                {'error': 'Dokumen tidak dalam status draft.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)
