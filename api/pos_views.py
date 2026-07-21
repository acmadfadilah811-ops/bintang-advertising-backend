import random
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from decimal import Decimal
from .pos_models import POSSale, POSSaleItem
from .pos_serializers import POSSaleSerializer
from .models import SaldoKasHarian, Contact
from .product_models import Product, ProductVariant, ProductStockMovement
from . import stock_fifo
from . import uom
from . import pos_settings
from . import spk
from .permissions import IsOwnerManagerAdminOrKasir, IsStrictOwnerOrManager
from .throttles import PasskeyRateThrottle
from .pos_services import create_sale, void_sale

class POSSaleViewSet(viewsets.ModelViewSet):
    queryset = POSSale.objects.all().order_by('-created_at')
    serializer_class = POSSaleSerializer
    permission_classes = [IsOwnerManagerAdminOrKasir]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        # Allow filtering by shift or kasir or status
        qs = super().get_queryset()
        shift_id = self.request.query_params.get('shift')
        kasir_id = self.request.query_params.get('kasir')
        status_val = self.request.query_params.get('status')

        if shift_id:
            qs = qs.filter(shift_id=shift_id)
        if kasir_id:
            qs = qs.filter(kasir_id=kasir_id)
        if status_val:
            qs = qs.filter(status=status_val)

        # Pembatasan visibilitas dari Pengaturan POS. Diterapkan di server agar
        # tidak bisa dilewati; pemilik/manajer tetap melihat semuanya.
        user = self.request.user
        is_atasan = getattr(user, 'role', '') in ('owner', 'manager') or user.is_superuser
        if not is_atasan:
            if pos_settings.staf_hanya_transaksi_hari_ini():
                qs = qs.filter(created_at__date=timezone.localdate())
            if pos_settings.sembunyikan_transaksi_perangkat_lain():
                qs = qs.filter(kasir=user)
        return qs

    @action(detail=False, methods=['post'], url_path='verify-passkey', throttle_classes=[PasskeyRateThrottle])
    def verify_passkey(self, request):
        """Verifikasi PIN PassKey untuk tindakan sensitif di POS.

        Body: {"aksi": "diskon|pelanggan|belum_bayar|sudah_bayar", "pin": "1234"}
        PIN dicocokkan di server supaya tidak bisa dibaca/dilewati dari browser.
        """
        aksi = request.data.get('aksi')
        if aksi not in pos_settings.PASSKEY_AKSI:
            return Response({'error': 'Aksi PassKey tidak dikenal.'}, status=status.HTTP_400_BAD_REQUEST)
        if not pos_settings.passkey_aktif(aksi):
            return Response({'ok': True, 'aktif': False})
        if pos_settings.passkey_cocok(aksi, request.data.get('pin')):
            return Response({'ok': True, 'aktif': True})
        return Response({'ok': False, 'aktif': True, 'error': 'PIN salah.'},
                        status=status.HTTP_403_FORBIDDEN)

    @action(detail=False, methods=['get'], url_path='pos-rules')
    def pos_rules(self, request):
        """Ringkasan aturan POS yang sedang aktif — dipakai UI kasir agar
        tampilannya selaras dengan yang ditegakkan server."""
        return Response({
            'blokir_stok_kosong': pos_settings.blokir_jual_jika_stok_kosong(),
            'blokir_harga_dibawah_beli': pos_settings.blokir_harga_dibawah_harga_beli(),
            'blokir_tahan_pesanan': pos_settings.blokir_tahan_pesanan(),
            'wajib_shift_aktif': pos_settings.wajib_shift_aktif(),
            'sembunyikan_stok': pos_settings.ext('hide_remaining_stock'),
            'sembunyikan_daftar_pelanggan': pos_settings.ext('hide_customer_list'),
            'disable_add_custom_item': pos_settings.ext('disable_add_custom_item'),
            'hide_splitbill': pos_settings.ext('hide_splitbill'),
            'blokir_cetak_ulang': pos_settings.ext('disable_reprint'),
            'blokir_cetak_pengecekan': pos_settings.ext('disable_print_checking'),
            'passkey': {
                aksi: pos_settings.passkey_aktif(aksi)
                for aksi in pos_settings.PASSKEY_AKSI
            },
        })

    def _validasi_aturan_pos(self, items, status_val):
        """Terapkan setelan Pengaturan POS sebelum transaksi dibuat.

        Divalidasi di depan (bukan sambil memotong stok) supaya tidak ada
        transaksi setengah jadi saat salah satu item melanggar aturan.
        Mengembalikan pesan error, atau None bila lolos.
        """
        cek_stok = pos_settings.blokir_jual_jika_stok_kosong()
        cek_harga = pos_settings.blokir_harga_dibawah_harga_beli()
        cek_kustom = pos_settings.ext('disable_add_custom_item')
        # Ketiganya harus ikut dalam guard ini. Kalau tidak, aturan item kustom
        # ikut terlewat begitu cek stok & harga sama-sama nonaktif.
        if not (cek_stok or cek_harga or cek_kustom):
            return None

        # Akumulasi qty per produk/varian: 2 baris produk sama harus dijumlahkan
        # dulu, kalau tidak masing-masing lolos padahal totalnya melebihi stok.
        butuh = {}
        for item in items:
            product_id = item.get('product_id')
            if not product_id:
                # Item kustom (non-katalog): tidak punya stok/harga beli untuk
                # divalidasi, jadi hanya aturan boleh-tidaknya yang diperiksa.
                if cek_kustom:
                    return "Penambahan item kustom (non-katalog) dinonaktifkan di Pengaturan POS."
                continue

            product = Product.objects.filter(id=product_id).first()
            if not product:
                return f"Produk dengan id {product_id} tidak ditemukan."
            variant = None
            if item.get('variant_id'):
                variant = ProductVariant.objects.filter(id=item.get('variant_id')).first()

            u = uom.resolve(product, item.get('uom_kode'), item.get('qty', 1),
                            item.get('harga', 0), variant)
            qty_dasar = Decimal(str(u['qty_dasar']))
            harga_dasar = u['harga_dasar']
            if harga_dasar is None:
                harga_dasar = Decimal(str(item.get('harga', 0) or 0))

            if cek_harga and harga_dasar < Decimal(str(product.harga_beli or 0)):
                return (f"'{product.nama}' tidak boleh dijual di bawah harga beli "
                        f"(harga beli Rp {product.harga_beli:,.0f}). "
                        f"Aturan ini aktif di Pengaturan POS.")

            if cek_stok and status_val == 'paid' and product.lacak_inventori:
                kunci = (product.id, variant.id if variant else None)
                b = butuh.setdefault(kunci, {'qty': Decimal('0'), 'produk': product, 'varian': variant})
                b['qty'] += qty_dasar

        if cek_stok and status_val == 'paid':
            for b in butuh.values():
                tersedia = Decimal(str((b['varian'] or b['produk']).qty_stok or 0))
                if b['qty'] > tersedia:
                    nama = b['produk'].nama + (f" ({b['varian'].nama_varian})" if b['varian'] else '')
                    return (f"Stok '{nama}' tidak mencukupi: tersedia {tersedia:g}, "
                            f"dibutuhkan {b['qty']:g}.")
        return None

    def create(self, request, *args, **kwargs):
        sale = create_sale(user=request.user, data=request.data)
        return Response(self.get_serializer(sale).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[IsStrictOwnerOrManager])
    def void(self, request, pk=None):
        sale = void_sale(sale_id=pk, user=request.user)
        return Response(self.get_serializer(sale).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='terbitkan-spk',
            permission_classes=[IsOwnerManagerAdminOrKasir])
    def terbitkan_spk(self, request, pk=None):
        """POST /api/pos/sales/{id}/terbitkan-spk/

        Menerbitkan SPK produksi untuk item transaksi POS — padanan
        /api/orders/{id}/assign/ pada alur pesanan. Dipakai terminal kasir
        saat melayani pesanan custom yang perlu dikerjakan divisi produksi.
        """
        sale = self.get_object()

        if sale.status != 'paid':
            return Response(
                {'error': 'Hanya transaksi lunas yang bisa diterbitkan SPK-nya.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item_ids = request.data.get('item_ids') or []
        items = sale.items.all()
        if item_ids:
            items = items.filter(pk__in=item_ids)

        try:
            biaya_desain = int(request.data.get('biaya_desain', 0) or 0)
            insentif = int(request.data.get('insentif', 0) or 0)
        except (TypeError, ValueError):
            return Response({'error': 'biaya_desain dan insentif harus berupa angka.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                staff = spk.resolve_staff(request.data.get('staff_id'), pemohon=request.user)
                tahap = spk.resolve_tahap(
                    tahap_id=request.data.get('tahap_id'),
                    divisi_id=request.data.get('divisi_id'),
                    staff=staff,
                )
                jobs = spk.terbitkan(
                    items, field='pos_sale_item', tahap=tahap, staff=staff,
                    biaya_desain=biaya_desain, insentif=insentif,
                )
        except spk.SpkError as exc:
            return Response({'error': exc.pesan}, status=exc.status_code)

        target = spk.nama_target(staff, tahap)
        return Response({
            'message': f'SPK nota {sale.nomor} berhasil diterbitkan ke {target}.',
            'jobs': jobs,
        }, status=status.HTTP_200_OK)
