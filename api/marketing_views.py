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

    @action(detail=False, methods=['post'], url_path='evaluate')
    def evaluate(self, request):
        from decimal import Decimal
        from .models import Contact
        from .product_models import Product
        from .promo_engine import BarisKeranjang, KonteksPromo, evaluate_coupon_code
        from .marketing_models import KANAL_POS

        kode = request.data.get('kode', '').strip()
        subtotal = request.data.get('subtotal', 0)
        pelanggan_id = request.data.get('pelanggan')

        pelanggan = None
        if pelanggan_id:
            pelanggan = Contact.objects.filter(pk=pelanggan_id).first()

        raw_items = request.data.get('items') or []
        baris = []
        for item in raw_items:
            pid = item.get('product_id') or item.get('product')
            prod = Product.objects.filter(pk=pid).first() if pid else None
            qty = Decimal(str(item.get('qty', 1) or 1))
            harga = Decimal(str(item.get('harga', 0) or 0))
            baris.append(BarisKeranjang(
                product=prod,
                qty=qty,
                harga=harga,
                subtotal=harga * qty
            ))

        konteks = KonteksPromo(
            baris=baris,
            subtotal=Decimal(str(subtotal or 0)),
            pelanggan=pelanggan,
            kanal=KANAL_POS,
        )
        hasil = evaluate_coupon_code(kode, konteks)
        if not hasil.ok:
            return Response({'ok': False, 'alasan': hasil.alasan}, status=400)

        return Response({
            'ok': True,
            'kupon': {
                'id': hasil.kupon.id,
                'kode': hasil.kupon.kode,
                'judul': hasil.kupon.judul,
                'tipe_diskon': hasil.kupon.tipe_diskon,
                'jumlah_diskon': float(hasil.kupon.jumlah_diskon),
                'maksimal_jumlah_diskon': float(hasil.kupon.maksimal_jumlah_diskon),
                'min_total_pesanan': float(hasil.kupon.min_total_pesanan),
            },
            'diskon': float(hasil.diskon),
            'alasan': hasil.alasan,
        })


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
