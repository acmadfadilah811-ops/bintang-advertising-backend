from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from ..models import SystemConfig, FAQ
from ..serializers import SystemConfigSerializer, FAQSerializer, BusinessSettingsSerializer
from ..permissions import IsOwnerManagerAdminOrReadOnly, IsStrictOwnerOrManager

class SystemConfigViewSet(viewsets.ModelViewSet):
    queryset = SystemConfig.objects.all()
    serializer_class = SystemConfigSerializer
    permission_classes = [IsStrictOwnerOrManager]

class FAQViewSet(viewsets.ModelViewSet):
    queryset = FAQ.objects.all()
    serializer_class = FAQSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]


class BusinessSettingsView(APIView):
    """
    GET  /api/business-settings/ — Baca pengaturan bisnis
    PATCH /api/business-settings/ — Update pengaturan bisnis (Owner/Manager only)

    Pengaturan disimpan di SystemConfig dengan prefix 'bisnis_'.
    Divisi digunakan sebagai satuan organisasi/departemen.
    """
    permission_classes = [IsStrictOwnerOrManager]

    def get(self, request):
        """Kembalikan semua pengaturan bisnis + daftar divisi."""
        serializer = BusinessSettingsSerializer(instance={}, context={'request': request})
        return Response(serializer.data)

    def patch(self, request):
        """Update pengaturan bisnis — hanya Owner atau Manager."""
        if getattr(request.user, 'role', '') not in ['owner', 'manager']:
            return Response(
                {'error': 'Hanya Owner atau Manager yang dapat mengubah pengaturan bisnis.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = BusinessSettingsSerializer(data=request.data, context={'request': request}, partial=True)
        if serializer.is_valid():
            serializer.save()
            # Kembalikan data terbaru setelah disimpan
            return Response(BusinessSettingsSerializer(instance={}, context={'request': request}).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
