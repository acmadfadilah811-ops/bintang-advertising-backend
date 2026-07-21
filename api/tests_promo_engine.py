"""Test mesin promo: kupon, promosi POS, dan diskon penjualan otomatis.

Fokusnya pada ATURAN, bukan CRUD — tiap test menyatakan satu keputusan bisnis
yang harus tetap benar: kapan potongan diberikan, sebesar apa, dan kapan
ditolak. Sebelum ada mesin ini, modul marketing tidak tersambung ke transaksi
sama sekali sehingga tidak ada satu pun perilaku yang bisa diuji.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api import promo_engine
from api.marketing_models import CouponUsage, DiscountCoupon, POSPromotion, SalesDiscount
from api.models import Contact
from api.pos_models import POSSale
from api.product_models import Brand, Product, ProductCategory

User = get_user_model()


def konteks(baris, pelanggan=None, kanal=promo_engine.KANAL_POS):
    subtotal = sum((b.subtotal for b in baris), Decimal('0'))
    return promo_engine.KonteksPromo(
        baris=baris, subtotal=promo_engine.money(subtotal),
        pelanggan=pelanggan, kanal=kanal)


def baris(product, qty, harga):
    qty = Decimal(str(qty))
    harga = Decimal(str(harga))
    return promo_engine.BarisKeranjang(
        product=product, variant=None, qty=qty, harga=harga,
        subtotal=promo_engine.money(harga * qty))


class BasisPromoTestCase(APITestCase):
    def setUp(self):
        self.hari_ini = timezone.localdate()
        self.kategori = ProductCategory.objects.create(nama='Spanduk')
        self.kategori_lain = ProductCategory.objects.create(nama='Sticker')
        self.brand = Brand.objects.create(nama='Brandy')
        self.produk = Product.objects.create(
            nama='Spanduk Flexi', harga_beli=10000, harga_jual_toko=50000,
            kategori=self.kategori, brand=self.brand, qty_stok=100)
        self.produk_lain = Product.objects.create(
            nama='Sticker Graftac', harga_beli=2000, harga_jual_toko=10000,
            kategori=self.kategori_lain, qty_stok=100)
        self.pelanggan = Contact.objects.create(nomor_wa='628111', nama='Budi')

    def kupon(self, **override):
        data = dict(
            kode='HEMAT10', judul='Hemat 10%', tanggal_aktif=self.hari_ini,
            tanpa_kadaluarsa=True, tipe_diskon='percent', jumlah_diskon=Decimal('10'),
            show_pos=True, show_online=True, is_active=True,
        )
        data.update(override)
        return DiscountCoupon.objects.create(**data)


class KuponTestCase(BasisPromoTestCase):

    def test_diskon_persen_dihitung_dari_subtotal(self):
        k = self.kupon()
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 2, 50000)]))
        self.assertTrue(hasil.ok, hasil.alasan)
        self.assertEqual(hasil.diskon, Decimal('10000.00'))  # 10% dari 100.000

    def test_maksimal_diskon_membatasi_potongan(self):
        k = self.kupon(maksimal_jumlah_diskon=Decimal('5000'))
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 2, 50000)]))
        self.assertEqual(hasil.diskon, Decimal('5000.00'))

    def test_diskon_nominal_tidak_pernah_melebihi_basis(self):
        """Nominal 999.999 atas belanja 10.000 tidak boleh membuat total minus."""
        k = self.kupon(tipe_diskon='nominal', jumlah_diskon=Decimal('999999'))
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk_lain, 1, 10000)]))
        self.assertEqual(hasil.diskon, Decimal('10000.00'))

    def test_kupon_belum_aktif_ditolak(self):
        k = self.kupon(tanggal_aktif=self.hari_ini + timedelta(days=3))
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 1, 50000)]))
        self.assertFalse(hasil.ok)
        self.assertIn('Belum aktif', hasil.alasan)

    def test_kupon_kedaluwarsa_ditolak(self):
        k = self.kupon(tanpa_kadaluarsa=False,
                       tanggal_kadaluarsa=self.hari_ini - timedelta(days=1))
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 1, 50000)]))
        self.assertFalse(hasil.ok)
        self.assertIn('kedaluwarsa', hasil.alasan)

    def test_minimal_belanja_belum_terpenuhi_ditolak(self):
        k = self.kupon(min_total_pesanan=Decimal('200000'))
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 1, 50000)]))
        self.assertFalse(hasil.ok)
        self.assertIn('Minimal belanja', hasil.alasan)

    def test_kanal_dihormati(self):
        k = self.kupon(show_pos=False, show_online=True)
        di_pos = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 1, 50000)]))
        self.assertFalse(di_pos.ok)
        di_online = promo_engine.evaluate_coupon(
            k, konteks([baris(self.produk, 1, 50000)], kanal=promo_engine.KANAL_ONLINE))
        self.assertTrue(di_online.ok, di_online.alasan)

    def test_cakupan_produk_membatasi_basis_diskon(self):
        """Hanya barang dalam cakupan yang jadi basis — bukan seluruh keranjang."""
        k = self.kupon()
        k.all_products = False
        k.save()
        k.produk.set([self.produk])
        hasil = promo_engine.evaluate_coupon(
            k, konteks([baris(self.produk, 1, 50000), baris(self.produk_lain, 1, 10000)]))
        self.assertTrue(hasil.ok, hasil.alasan)
        self.assertEqual(hasil.basis, Decimal('50000.00'))
        self.assertEqual(hasil.diskon, Decimal('5000.00'))

    def test_cakupan_kategori_ikut_dicocokkan(self):
        k = self.kupon()
        k.all_products = False
        k.save()
        k.grup_produk.set([self.kategori_lain])
        hasil = promo_engine.evaluate_coupon(
            k, konteks([baris(self.produk, 1, 50000), baris(self.produk_lain, 2, 10000)]))
        self.assertEqual(hasil.basis, Decimal('20000.00'))

    def test_keranjang_di_luar_cakupan_ditolak(self):
        k = self.kupon()
        k.all_products = False
        k.save()
        k.produk.set([self.produk])
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk_lain, 1, 10000)]))
        self.assertFalse(hasil.ok)
        self.assertIn('cakupan', hasil.alasan)

    def test_batas_penggunaan_habis_ditolak(self):
        k = self.kupon(unlimited_usage=False, batas_penggunaan=1)
        CouponUsage.objects.create(kupon=k, tanggal=self.hari_ini)
        hasil = promo_engine.evaluate_coupon(k, konteks([baris(self.produk, 1, 50000)]))
        self.assertFalse(hasil.ok)
        self.assertIn('Kuota', hasil.alasan)

    def test_once_per_customer_ditegakkan_dari_riwayat(self):
        k = self.kupon(once_per_customer=True)
        CouponUsage.objects.create(kupon=k, pelanggan=self.pelanggan, tanggal=self.hari_ini)
        hasil = promo_engine.evaluate_coupon(
            k, konteks([baris(self.produk, 1, 50000)], pelanggan=self.pelanggan))
        self.assertFalse(hasil.ok)
        self.assertIn('sudah pernah', hasil.alasan)

    def test_daily_reuse_mati_menolak_pemakaian_kedua_di_hari_sama(self):
        k = self.kupon(daily_reuse=False)
        CouponUsage.objects.create(kupon=k, pelanggan=self.pelanggan, tanggal=self.hari_ini)
        hasil = promo_engine.evaluate_coupon(
            k, konteks([baris(self.produk, 1, 50000)], pelanggan=self.pelanggan))
        self.assertFalse(hasil.ok)
        self.assertIn('sekali per hari', hasil.alasan)

    def test_daily_reuse_aktif_mengizinkan_pemakaian_berulang(self):
        k = self.kupon(daily_reuse=True)
        CouponUsage.objects.create(kupon=k, pelanggan=self.pelanggan, tanggal=self.hari_ini)
        hasil = promo_engine.evaluate_coupon(
            k, konteks([baris(self.produk, 1, 50000)], pelanggan=self.pelanggan))
        self.assertTrue(hasil.ok, hasil.alasan)

    def test_kode_dicocokkan_case_insensitive(self):
        self.kupon(kode='HEMAT10')
        hasil = promo_engine.evaluate_coupon_code('hemat10', konteks([baris(self.produk, 1, 50000)]))
        self.assertTrue(hasil.ok, hasil.alasan)

    def test_kode_tidak_dikenal_memberi_alasan_jelas(self):
        hasil = promo_engine.evaluate_coupon_code('NGAWUR', konteks([baris(self.produk, 1, 50000)]))
        self.assertFalse(hasil.ok)
        self.assertIn('tidak ditemukan', hasil.alasan)


class PromosiPOSTestCase(BasisPromoTestCase):

    def promo(self, **override):
        data = dict(judul='Promo', tipe_promosi='DA', tanggal_aktif=self.hari_ini,
                    tanpa_kadaluarsa=True, jam_24=True, is_active=True,
                    hari='min,sen,sel,rab,kam,jum,sab')
        data.update(override)
        return POSPromotion.objects.create(**data)

    def test_da_memberi_diskon_saat_ambang_terpenuhi(self):
        p = self.promo(tipe_promosi='DA', min_total_transaksi=Decimal('100000'),
                       tipe_diskon='percent', jumlah_diskon=Decimal('10'))
        hasil = promo_engine.evaluate_promotions(konteks([baris(self.produk, 2, 50000)]), [p])
        self.assertEqual(hasil.diskon, Decimal('10000.00'))

    def test_da_tidak_menyala_di_bawah_ambang(self):
        p = self.promo(tipe_promosi='DA', min_total_transaksi=Decimal('100000'),
                       tipe_diskon='percent', jumlah_diskon=Decimal('10'))
        hasil = promo_engine.evaluate_promotions(konteks([baris(self.produk, 1, 50000)]), [p])
        self.assertEqual(hasil.diskon, Decimal('0'))

    def test_bx_memberi_item_gratis_sesuai_pemicu(self):
        p = self.promo(tipe_promosi='BX', combine_qty=True, combine_qty_value=2,
                       qty_gratis=1, berlaku_kelipatan=False)
        p.produk_qty = [{'nama': self.produk.nama, 'qty': 2, 'product_id': self.produk.pk}]
        p.save()
        p.produk_gratis.set([self.produk_lain])
        hasil = promo_engine.evaluate_promotions(konteks([baris(self.produk, 4, 50000)]), [p])
        self.assertEqual(len(hasil.items_gratis), 1)
        # kelipatan mati -> pemicu dipatok 1 meski beli 4 (cukup untuk 2x)
        self.assertEqual(hasil.items_gratis[0].qty, Decimal('1'))

    def test_bx_kelipatan_menggandakan_hadiah(self):
        p = self.promo(tipe_promosi='BX', combine_qty=True, combine_qty_value=2,
                       qty_gratis=1, berlaku_kelipatan=True)
        p.produk_qty = [{'nama': self.produk.nama, 'qty': 2, 'product_id': self.produk.pk}]
        p.save()
        p.produk_gratis.set([self.produk_lain])
        hasil = promo_engine.evaluate_promotions(konteks([baris(self.produk, 4, 50000)]), [p])
        self.assertEqual(hasil.items_gratis[0].qty, Decimal('2'))

    def test_dq_hanya_mendiskon_produk_yang_disyaratkan(self):
        p = self.promo(tipe_promosi='DQ', combine_qty=True, combine_qty_value=2,
                       tipe_diskon='percent', jumlah_diskon=Decimal('50'))
        p.produk_qty = [{'nama': self.produk.nama, 'qty': 2, 'product_id': self.produk.pk}]
        p.save()
        hasil = promo_engine.evaluate_promotions(
            konteks([baris(self.produk, 2, 50000), baris(self.produk_lain, 5, 10000)]), [p])
        # 50% dari 100.000 (hanya produk yang disyaratkan), bukan dari 150.000
        self.assertEqual(hasil.diskon, Decimal('50000.00'))

    def test_promo_di_luar_hari_berlaku_dilewati(self):
        kode_hari_ini = promo_engine.KODE_HARI[timezone.localtime().weekday()]
        lain = ','.join(h for h in promo_engine.KODE_HARI if h != kode_hari_ini)
        p = self.promo(tipe_promosi='DA', min_total_transaksi=Decimal('1'),
                       jumlah_diskon=Decimal('10'), hari=lain)
        hasil = promo_engine.evaluate_promotions(konteks([baris(self.produk, 1, 50000)]), [p])
        self.assertEqual(hasil.diskon, Decimal('0'))

    def test_produk_qty_tanpa_product_id_tidak_menyalakan_promo(self):
        """Entri lama yang namanya tak ketemu saat migrasi tidak boleh
        dicocokkan ulang berdasarkan teks — promo bisa menyala keliru."""
        p = self.promo(tipe_promosi='BX', combine_qty=True, combine_qty_value=1, qty_gratis=1)
        p.produk_qty = [{'nama': self.produk.nama, 'qty': 1, 'product_id': None}]
        p.save()
        p.produk_gratis.set([self.produk_lain])
        hasil = promo_engine.evaluate_promotions(konteks([baris(self.produk, 5, 50000)]), [p])
        # Tanpa product_id, syarat produk kosong -> jatuh ke "keranjang tidak
        # kosong", jadi hadiah tetap 1x, bukan 5x hasil pencocokan nama.
        self.assertEqual(len(hasil.items_gratis), 1)
        self.assertEqual(hasil.items_gratis[0].qty, Decimal('1'))


class DiskonPenjualanTestCase(BasisPromoTestCase):

    def test_hanya_berlaku_di_kanal_online(self):
        SalesDiscount.objects.create(
            tanggal_aktif=self.hari_ini, tanpa_kadaluarsa=True,
            minimal_total_pesanan=Decimal('50000'), tipe_diskon='percent',
            jumlah_diskon=Decimal('10'), is_active=True)
        di_pos, _ = promo_engine.evaluate_sales_discount(konteks([baris(self.produk, 2, 50000)]))
        self.assertEqual(di_pos, Decimal('0'))
        di_online, _ = promo_engine.evaluate_sales_discount(
            konteks([baris(self.produk, 2, 50000)], kanal=promo_engine.KANAL_ONLINE))
        self.assertEqual(di_online, Decimal('10000.00'))

    def test_memilih_aturan_paling_menguntungkan_pelanggan(self):
        """Hasil tidak boleh bergantung urutan baris di tabel."""
        for jumlah in (5, 20, 12):
            SalesDiscount.objects.create(
                tanggal_aktif=self.hari_ini, tanpa_kadaluarsa=True,
                minimal_total_pesanan=Decimal('1'), tipe_diskon='percent',
                jumlah_diskon=Decimal(jumlah), is_active=True)
        nilai, _ = promo_engine.evaluate_sales_discount(
            konteks([baris(self.produk, 2, 50000)], kanal=promo_engine.KANAL_ONLINE))
        self.assertEqual(nilai, Decimal('20000.00'))  # 20%, yang terbesar


class IntegrasiPOSTestCase(BasisPromoTestCase):
    """Kupon harus benar-benar mengubah nota, bukan sekadar dihitung."""

    def setUp(self):
        super().setUp()
        self.kasir = User.objects.create_user(username='kasir1', password='rahasia123',
                                              role='kasir')
        self.client.force_authenticate(user=self.kasir)

    def _payload(self, **override):
        data = {
            'items': [{'product_id': self.produk.pk, 'qty': 2}],
            'dibayar': 100000,
            'status': 'paid',
        }
        data.update(override)
        return data

    def test_kupon_mengurangi_total_nota_dan_tercatat(self):
        self.kupon()
        res = self.client.post('/api/pos/sales/', self._payload(kupon_kode='HEMAT10'),
                               format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        sale = POSSale.objects.get(pk=res.data['id'])
        self.assertEqual(sale.subtotal, Decimal('100000.00'))
        self.assertEqual(sale.diskon_kupon, Decimal('10000.00'))
        self.assertEqual(sale.total, Decimal('90000.00'))
        self.assertEqual(CouponUsage.objects.filter(pos_sale=sale).count(), 1)

    def test_kupon_tidak_valid_menggagalkan_transaksi(self):
        """Gagal keras: kasir sudah menjanjikan potongan itu ke pelanggan."""
        self.kupon(min_total_pesanan=Decimal('999999'))
        res = self.client.post('/api/pos/sales/', self._payload(kupon_kode='HEMAT10'),
                               format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Kupon ditolak', str(res.data))
        self.assertEqual(POSSale.objects.count(), 0)

    def test_void_mengembalikan_kuota_kupon(self):
        k = self.kupon(unlimited_usage=False, batas_penggunaan=1)
        res = self.client.post('/api/pos/sales/', self._payload(kupon_kode='HEMAT10'),
                               format='json')
        sale_id = res.data['id']
        self.assertEqual(CouponUsage.objects.count(), 1)

        owner = User.objects.create_user(username='bos', password='rahasia123', role='owner')
        self.client.force_authenticate(user=owner)
        self.client.post(f'/api/pos/sales/{sale_id}/void/', {}, format='json')

        self.assertEqual(CouponUsage.objects.count(), 0)
        k.refresh_from_db()
        self.assertEqual(k.penggunaan_count, 0)

    def test_preview_tidak_menyimpan_apa_pun(self):
        self.kupon()
        res = self.client.post('/api/promo/preview/', {
            'kanal': 'pos', 'kupon_kode': 'HEMAT10',
            'items': [{'product_id': self.produk.pk, 'qty': 2}],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(Decimal(str(res.data['diskon_kupon'])), Decimal('10000.00'))
        self.assertEqual(CouponUsage.objects.count(), 0)
        self.assertEqual(POSSale.objects.count(), 0)
