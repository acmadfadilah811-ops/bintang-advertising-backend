from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from api.permissions import IsOwnerManagerAdminOrReadOnly

from .marketing_models import SalesDiscount, DiscountCoupon, POSPromotion
from .marketing_serializers import (
    SalesDiscountSerializer, DiscountCouponSerializer, POSPromotionSerializer
)


class SalesDiscountViewSet(viewsets.ModelViewSet):
    """Diskon Penjualan: Marketing > Voucher & Diskon > Diskon Penjualan (khusus Toko Online)."""
    queryset = SalesDiscount.objects.all()
    serializer_class = SalesDiscountSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)

    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        instance = self.get_object()
        instance.is_active = not instance.is_active
        instance.save()
        return Response(self.get_serializer(instance).data)


class DiscountCouponViewSet(viewsets.ModelViewSet):
    """Kupon Diskon: Marketing > Voucher & Diskon > Kupon Diskon."""
    queryset = DiscountCoupon.objects.all()
    serializer_class = DiscountCouponSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)

    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        instance = self.get_object()
        instance.is_active = not instance.is_active
        instance.save()
        return Response(self.get_serializer(instance).data)


class POSPromotionViewSet(viewsets.ModelViewSet):
    """Promosi (POS): Marketing > Voucher & Diskon > Promosi (POS) — tipe BX/DQ/DA/FI."""
    queryset = POSPromotion.objects.all()
    serializer_class = POSPromotionSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)

    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        instance = self.get_object()
        instance.is_active = not instance.is_active
        instance.save()
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=['post'], url_path='duplicate')
    def duplicate(self, request, pk=None):
        """Dipakai tombol "Salin Diskon" — duplikasi promosi yang sudah ada jadi draft baru
        (judul diberi suffix "(Copy)") agar tinggal disesuaikan lalu disimpan."""
        instance = self.get_object()
        instance.pk = None
        instance._state.adding = True
        instance.judul = f"{instance.judul} (Copy)"
        instance.dibuat_oleh = request.user
        instance.save()
        return Response(self.get_serializer(instance).data, status=201)
