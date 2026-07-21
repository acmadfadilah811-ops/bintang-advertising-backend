from rest_framework import serializers
from .marketing_models import SalesDiscount, DiscountCoupon, POSPromotion

# --------------------------------------------------------------------------
# Nama field yang dulu bertipe CharField, kini menjadi ManyToManyField setelah
# migrasi 0078.  Frontend masih mengirim string (misal pelanggan: "").  Kita
# harus mengalihkan nilai string itu ke kolom *_legacy dan mengosongkan M2M
# agar DRF tidak menolak tipe data yang salah.
# --------------------------------------------------------------------------
_COUPON_M2M = ['pelanggan', 'produk', 'grup_produk', 'paket_produk', 'brand']
_DISCOUNT_M2M = ['brand']
_PROMO_M2M = ['pelanggan', 'grup_produk', 'paket_produk', 'brand', 'produk_gratis']


def _redirect_string_to_legacy(data, m2m_names):
    """Salin nilai string M2M → *_legacy, lalu buang dari data agar
    DRF tidak mencoba mem-parse string sebagai list PK."""
    mutable = data.copy() if hasattr(data, 'copy') else dict(data)
    for name in m2m_names:
        val = mutable.get(name)
        if isinstance(val, str):
            # Simpan di kolom legacy yang masih CharField
            legacy = f'{name}_legacy'
            mutable[legacy] = val
            # Hapus dari data agar M2M field tidak divalidasi
            del mutable[name]
        elif val is None:
            # Abaikan None untuk M2M
            mutable.pop(name, None)
    return mutable


class SalesDiscountSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = SalesDiscount
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']

    def to_internal_value(self, data):
        data = _redirect_string_to_legacy(data, _DISCOUNT_M2M)
        return super().to_internal_value(data)


class DiscountCouponSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = DiscountCoupon
        fields = '__all__'
        read_only_fields = ['dibuat_oleh', 'penggunaan_count']

    def to_internal_value(self, data):
        data = _redirect_string_to_legacy(data, _COUPON_M2M)
        return super().to_internal_value(data)


class POSPromotionSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.ReadOnlyField(source='dibuat_oleh.username')

    class Meta:
        model = POSPromotion
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']

    def to_internal_value(self, data):
        data = _redirect_string_to_legacy(data, _PROMO_M2M)
        return super().to_internal_value(data)

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
