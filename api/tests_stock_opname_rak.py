"""Test pewarisan Rak pada Stok Opname.

Rak melekat di produk/varian (Produk > Lacak Inventori), bukan diketik per item
opname — kolom Rack di layar opname memang read-only. Karena itu bulk-add-items
harus mewarisinya dari produk/varian, seperti halnya stok_sistem.

Sebelum perbaikan ini bulk_add_items tidak menyalin rak sama sekali, jadi produk
yang ditambahkan lewat "Tambah Produk" selalu ber-rak kosong dan kolom Rack
menampilkan '-' walau rak produknya sudah diisi.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api.product_models import Product, ProductCategory, ProductVariant

User = get_user_model()


class StockOpnameRakTest(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner_opname_rak', password='password123', role='owner'
        )
        self.client.force_authenticate(user=self.owner)

        self.kategori = ProductCategory.objects.create(nama='Banner', key='banner')
        self.produk = Product.objects.create(
            nama='Spanduk Flexi', kategori=self.kategori, sku='BNR-280',
            qty_stok=25, rack='A-01', lacak_inventori=True,
        )

    def _buat_dokumen(self):
        res = self.client.post(
            '/api/stock-opname-documents/', {'tanggal': '2026-07-16', 'catatan': 'test'}
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return res.data['id']

    def _tambah(self, doc_id, entry, jam='08:00'):
        return self.client.post(
            f'/api/stock-opname-documents/{doc_id}/bulk-add-items/',
            {'products': [entry], 'jam_opname': jam},
            format='json',
        )

    def test_rak_produk_diwarisi(self):
        doc_id = self._buat_dokumen()
        res = self._tambah(doc_id, {'product': self.produk.id})

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['created'][0]['rak'], 'A-01')
        # stok_sistem sudah diwarisi sejak awal — dijaga sekalian agar tidak
        # ikut rusak saat baris rak ditambahkan di sebelahnya.
        self.assertEqual(str(res.data['created'][0]['stok_sistem']), '25.00')

    def test_rak_varian_menang_atas_rak_produk(self):
        produk_var = Product.objects.create(
            nama='Stiker Vinyl', kategori=self.kategori, sku='STK-VNL',
            qty_stok=0, rack='A-01', has_variant=True, lacak_inventori=True,
        )
        varian = ProductVariant.objects.create(
            product=produk_var, nama_varian='Glossy', qty_stok=80, rack='B-07',
        )

        doc_id = self._buat_dokumen()
        res = self._tambah(doc_id, {'product': produk_var.id, 'variant': varian.id})

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # Rak varian, bukan rak produk induknya — 'owner' adalah varian bila ada.
        self.assertEqual(res.data['created'][0]['rak'], 'B-07')
        self.assertEqual(str(res.data['created'][0]['stok_sistem']), '80.00')

    def test_produk_tanpa_rak_tetap_kosong(self):
        polos = Product.objects.create(
            nama='Banner Roll Up', kategori=self.kategori, sku='BNR-RU',
            qty_stok=5, lacak_inventori=True,
        )
        doc_id = self._buat_dokumen()
        res = self._tambah(doc_id, {'product': polos.id})

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['created'][0]['rak'], '')
