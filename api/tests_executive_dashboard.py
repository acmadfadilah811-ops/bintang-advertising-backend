"""Dashboard eksekutif.

Fokus: angka yang disajikan benar-benar berasal dari data (bukan taksiran),
dan hanya manajemen yang boleh melihatnya.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .pos_models import POSSale, POSSaleItem
from .product_models import Product, ProductStockMovement

URL = '/api/executive-dashboard/'


class ExecutiveDashboardTest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='owner', password='x', role='owner')
        self.kasir = User.objects.create_user(username='kasir', password='x', role='kasir')
        self.produk = Product.objects.create(
            nama='Kaos', harga_beli=Decimal('40000'), harga_jual_toko=Decimal('100000'),
            qty_stok=Decimal('10'), lacak_inventori=True, stok_minimum=Decimal('2'),
        )

    def _jual(self, total, hpp, status='paid'):
        sale = POSSale.objects.create(nomor=f'POS-{total}-{id(self)}', total=Decimal(total), status=status)
        POSSaleItem.objects.create(
            sale=sale, product=self.produk, nama_snapshot='Kaos',
            harga_snapshot=Decimal(total), qty=Decimal('1'), subtotal=Decimal(total),
        )
        ProductStockMovement.objects.create(
            product=self.produk, tipe='penjualan', qty=Decimal('1'),
            hpp_total=Decimal(hpp), stok_awal=Decimal('10'), stok_akhir=Decimal('9'),
        )
        return sale

    def test_kasir_tidak_boleh_melihat(self):
        self.client.force_authenticate(self.kasir)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_periode_tidak_dikenal_ditolak_400(self):
        self.client.force_authenticate(self.owner)
        res = self.client.get(URL, {'period': 'sepanjang-masa'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('tidak dikenal', res.json()['error'])

    def test_laba_kotor_dari_hpp_nyata(self):
        # Pendapatan 100.000, HPP 40.000 -> laba kotor 60.000.
        # Angka ini harus benar-benar dihitung, bukan persentase tetap.
        self._jual('100000', '40000')
        self.client.force_authenticate(self.owner)
        kpi = {k['key']: k['value'] for k in self.client.get(URL).json()['kpi']}
        self.assertEqual(kpi['pendapatan'], 100000.0)
        self.assertEqual(kpi['hpp'], 40000.0)
        self.assertEqual(kpi['laba_kotor'], 60000.0)

    def test_pengembalian_mengurangi_hpp(self):
        self._jual('100000', '40000')
        ProductStockMovement.objects.create(
            product=self.produk, tipe='pengembalian', qty=Decimal('1'),
            hpp_total=Decimal('40000'), stok_awal=Decimal('9'), stok_akhir=Decimal('10'),
        )
        self.client.force_authenticate(self.owner)
        kpi = {k['key']: k['value'] for k in self.client.get(URL).json()['kpi']}
        self.assertEqual(kpi['hpp'], 0.0)

    def test_pos_belum_lunas_tidak_dihitung(self):
        self._jual('100000', '40000', status='hold')
        self.client.force_authenticate(self.owner)
        kpi = {k['key']: k['value'] for k in self.client.get(URL).json()['kpi']}
        self.assertEqual(kpi['pendapatan'], 0.0)

    def test_stok_dan_metrik_tak_tersedia_dijelaskan(self):
        self.client.force_authenticate(self.owner)
        data = self.client.get(URL).json()
        self.assertEqual(data['stok']['nilai_persediaan'], 400000.0)  # 10 x 40.000
        self.assertEqual(data['stok']['sehat'], 1)
        # Metrik yang butuh buku besar harus diumumkan, bukan diisi angka karangan.
        labels = [u['label'] for u in data['unavailable']]
        self.assertIn('Laba bersih', labels)
        self.assertTrue(all(u['reason'] for u in data['unavailable']))

    def test_delta_none_saat_tidak_ada_pembanding(self):
        self._jual('100000', '40000')
        self.client.force_authenticate(self.owner)
        kpi = {k['key']: k for k in self.client.get(URL).json()['kpi']}
        self.assertIsNone(kpi['pendapatan']['delta'])
