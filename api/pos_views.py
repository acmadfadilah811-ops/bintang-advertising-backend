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
from .permissions import IsOwnerManagerAdminOrKasir

class POSSaleViewSet(viewsets.ModelViewSet):
    queryset = POSSale.objects.all().order_by('-created_at')
    serializer_class = POSSaleSerializer
    permission_classes = [permissions.IsAuthenticated]

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

    @action(detail=False, methods=['post'], url_path='verify-passkey')
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
        items = request.data.get('items', [])
        if not items:
            return Response({'error': 'Tidak ada item dalam keranjang.'}, status=status.HTTP_400_BAD_REQUEST)

        status_val_awal = request.data.get('status', 'paid')

        # Ext Settings: pesanan tidak boleh ditahan/antri.
        if status_val_awal == 'hold' and pos_settings.blokir_tahan_pesanan():
            return Response(
                {'error': 'Menahan/mengantre pesanan dinonaktifkan di Pengaturan POS. '
                          'Selesaikan pembayaran terlebih dahulu.'},
                status=status.HTTP_400_BAD_REQUEST)

        # Menu Shift: transaksi hanya boleh saat ada shift terbuka.
        if pos_settings.wajib_shift_aktif():
            ada_shift = SaldoKasHarian.objects.filter(
                kas_akhir__isnull=True, waktu_tutup__isnull=True
            ).exists()
            if not ada_shift:
                return Response(
                    {'error': 'Belum ada shift yang dibuka. Buka shift dulu sebelum transaksi '
                              '(aturan aktif di Pengaturan POS > Shift).'},
                    status=status.HTTP_400_BAD_REQUEST)

        pelanggaran = self._validasi_aturan_pos(items, status_val_awal)
        if pelanggaran:
            return Response({'error': pelanggaran}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # Auto generate nomor transaksi
                now = timezone.now()
                # Ext Settings "No. Order reset setiap hari": nomor berurutan
                # per tanggal (POS-YYYYMMDD-0001). Default tetap pola lama.
                if pos_settings.nomor_order_reset_harian():
                    prefix = f"POS-{now.strftime('%Y%m%d')}-"
                    terakhir = (POSSale.objects.filter(nomor__startswith=prefix)
                                .order_by('-nomor').first())
                    urut = 1
                    if terakhir:
                        try:
                            urut = int(terakhir.nomor[len(prefix):]) + 1
                        except ValueError:
                            urut = 1
                    nomor = f"{prefix}{urut:04d}"
                else:
                    nomor = f"POS-{now.strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"
                
                # Shift terbuka = belum diisi kas akhir DAN belum ditutup.
                # Dahulukan shift milik pengguna yang sedang login supaya
                # penjualan tidak tertaut ke shift kasir lain saat beberapa
                # kasir membuka shift bersamaan. Bila pengguna ini tidak punya
                # shift sendiri (mis. owner menjalankan POS untuk shift kasir
                # lain), pakai shift terbuka mana pun seperti perilaku lama.
                shift_terbuka = SaldoKasHarian.objects.filter(
                    kas_akhir__isnull=True, waktu_tutup__isnull=True
                )
                shift = (shift_terbuka.filter(kasir=request.user).last()
                         or shift_terbuka.last())
                
                # Buat header penjualan
                subtotal = request.data.get('subtotal', 0)
                diskon = request.data.get('diskon', 0)
                pajak = request.data.get('pajak', 0)
                total = request.data.get('total', 0)
                dibayar = request.data.get('dibayar', 0)
                kembalian = request.data.get('kembalian', 0)
                metode_bayar = request.data.get('metode_bayar', 'Cash')
                catatan = request.data.get('catatan', '')
                status_val = request.data.get('status', 'paid')
                pelanggan_id = request.data.get('pelanggan')

                sale = POSSale.objects.create(
                    nomor=nomor,
                    kasir=request.user,
                    pelanggan_id=pelanggan_id,
                    shift=shift,
                    subtotal=subtotal,
                    diskon=diskon,
                    pajak=pajak,
                    total=total,
                    metode_bayar=metode_bayar,
                    dibayar=dibayar,
                    kembalian=kembalian,
                    catatan=catatan,
                    status=status_val
                )

                for item in items:
                    product_id = item.get('product_id')
                    variant_id = item.get('variant_id')
                    qty = float(item.get('qty', 1))
                    
                    if not product_id:
                        # Item Kustom: Simpan langsung snapshot tanpa update stok/FIFO
                        nama_snapshot = item.get('nama', 'Item Kustom')
                        harga_snapshot = float(item.get('harga', 0))
                        subtotal_item = harga_snapshot * qty
                        
                        POSSaleItem.objects.create(
                            sale=sale,
                            product=None,
                            variant=None,
                            nama_snapshot=nama_snapshot,
                            harga_snapshot=harga_snapshot,
                            qty=qty,
                            subtotal=subtotal_item,
                            catatan=item.get('catatan', ''),
                            uom_kode='',
                            uom_konverter=Decimal('1'),
                            uom_qty=qty,
                            uom_harga=harga_snapshot,
                        )
                        continue

                    # Lock row product
                    product = Product.objects.select_for_update().get(id=product_id)
                    variant = ProductVariant.objects.select_for_update().get(id=variant_id) if variant_id else None
                    
                    nama_snapshot = item.get('nama', product.nama)
                    harga_input = float(item.get('harga', 0))

                    # Satuan alternatif (UOM): qty & harga dikonversi ke satuan
                    # DASAR supaya potong stok, FIFO, dan laporan tetap konsisten.
                    u = uom.resolve(product, item.get('uom_kode'), qty, harga_input, variant)
                    qty = float(u['qty_dasar'])
                    harga_snapshot = float(u['harga_dasar']) if u['harga_dasar'] is not None else harga_input
                    subtotal_item = harga_snapshot * qty

                    POSSaleItem.objects.create(
                        sale=sale,
                        product=product,
                        variant=variant,
                        nama_snapshot=nama_snapshot,
                        harga_snapshot=harga_snapshot,
                        qty=qty,
                        subtotal=subtotal_item,
                        catatan=item.get('catatan', ''),
                        uom_kode=u['uom_kode'],
                        uom_konverter=u['uom_konverter'],
                        uom_qty=u['uom_qty'],
                        uom_harga=harga_input if u['uom_kode'] else None,
                    )
                    
                    # Potong stok jika status paid/lunas.
                    # Mode Stok POS 'manual'/'off' membuat POS tidak menyentuh
                    # stok sama sekali — perubahan stok hanya lewat dokumen stok.
                    if status_val == 'paid' and pos_settings.pos_mengurangi_stok():
                        if variant:
                            if product.lacak_inventori:
                                stok_awal = variant.qty_stok
                                variant.qty_stok -= Decimal(str(qty))
                                variant.save()
                                product.qty_stok -= Decimal(str(qty))
                                product.save()
                                
                                mv = ProductStockMovement.objects.create(
                                    product=product,
                                    variant=variant,
                                    user=request.user,
                                    tipe='penjualan',
                                    qty=Decimal(str(qty)),
                                    stok_awal=stok_awal,
                                    stok_akhir=variant.qty_stok,
                                    catatan=f"Penjualan POS {sale.nomor}",
                                    tanggal=now.date(),
                                )
                                stock_fifo.consume_layers(product, variant, Decimal(str(qty)), movement=mv)
                        else:
                            if product.lacak_inventori:
                                stok_awal = product.qty_stok
                                product.qty_stok -= Decimal(str(qty))
                                product.save()
                                
                                mv = ProductStockMovement.objects.create(
                                    product=product,
                                    user=request.user,
                                    tipe='penjualan',
                                    qty=Decimal(str(qty)),
                                    stok_awal=stok_awal,
                                    stok_akhir=product.qty_stok,
                                    catatan=f"Penjualan POS {sale.nomor}",
                                    tanggal=now.date(),
                                )
                                stock_fifo.consume_layers(product, None, Decimal(str(qty)), movement=mv)
                
                serializer = self.get_serializer(sale)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    from rest_framework.decorators import action

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        sale = self.get_object()
        if sale.status == 'void':
            return Response({'error': 'Transaksi sudah dibatalkan sebelumnya.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                # Kembalikan stok jika sebelumnya status paid/lunas.
                # Konsisten dengan create: bila POS tidak memotong stok
                # (mode manual/off), void juga tidak boleh menambah stok.
                if sale.status == 'paid' and pos_settings.pos_mengurangi_stok():
                    for item in sale.items.all():
                        if item.product:
                            product = Product.objects.select_for_update().get(id=item.product.id)
                            variant = ProductVariant.objects.select_for_update().get(id=item.variant.id) if item.variant else None
                            qty = float(item.qty)

                            if variant:
                                if product.lacak_inventori:
                                    stok_awal = variant.qty_stok
                                    variant.qty_stok += Decimal(str(qty))
                                    variant.save()
                                    product.qty_stok += Decimal(str(qty))
                                    product.save()
                                    
                                    ProductStockMovement.objects.create(
                                        product=product,
                                        variant=variant,
                                        user=request.user,
                                        tipe='pengembalian',
                                        qty=Decimal(str(qty)),
                                        stok_awal=stok_awal,
                                        stok_akhir=variant.qty_stok,
                                        catatan=f"Pembatalan POS (Void) {sale.nomor}",
                                        tanggal=timezone.now().date(),
                                    )
                                    stock_fifo.create_layer(
                                        product, variant, Decimal(str(qty)),
                                        item.harga_snapshot or product.harga_beli,
                                        timezone.now().date(),
                                        sumber_tipe='pos_void', sumber_nomor=sale.nomor,
                                    )
                            else:
                                if product.lacak_inventori:
                                    stok_awal = product.qty_stok
                                    product.qty_stok += Decimal(str(qty))
                                    product.save()
                                    
                                    ProductStockMovement.objects.create(
                                        product=product,
                                        user=request.user,
                                        tipe='pengembalian',
                                        qty=Decimal(str(qty)),
                                        stok_awal=stok_awal,
                                        stok_akhir=product.qty_stok,
                                        catatan=f"Pembatalan POS (Void) {sale.nomor}",
                                        tanggal=timezone.now().date(),
                                    )
                                    stock_fifo.create_layer(
                                        product, None, Decimal(str(qty)),
                                        item.harga_snapshot or product.harga_beli,
                                        timezone.now().date(),
                                        sumber_tipe='pos_void', sumber_nomor=sale.nomor,
                                    )

                sale.status = 'void'
                sale.save()
                
                serializer = self.get_serializer(sale)
                return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

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
                staff = spk.resolve_staff(request.data.get('staff_id'))
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
