"""Test import CSV Stok Masuk.

Dua aturan yang diuji di sini:
1. Supplier yang ditulis di CSV harus sudah terdaftar di master Supplier
   (mengikuti perilaku Olsera). Kolom kosong tetap boleh.
2. Jumlah baris dibatasi CSV_IMPORT_MAX_ROWS.

Keduanya harus menolak SEBELUM item apa pun dibuat — bukan setengah jalan.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from api.customer_models import Supplier
from api.product_models import Product, ProductCategory, StockInDocument

User = get_user_model()

CSV_HEADER = 'product,variant,sku,supplier,qty,new_buy_price,rack'


def csv_file(*baris):
    isi = '\n'.join([CSV_HEADER, *baris])
    return SimpleUploadedFile('stok-masuk.csv', isi.encode('utf-8'), content_type='text/csv')


class StockInImportCsvTest(APITestCase):
    def setUp(self):
        # StockInDocumentViewSet memakai IsOwnerManagerAdminOrReadOnly,
        # jadi yang boleh menulis hanya owner/manager/admin.
        self.owner = User.objects.create_user(
            username='owner_import', password='password123', role='owner'
        )
        self.client.force_authenticate(user=self.owner)

        self.kategori = ProductCategory.objects.create(nama='Banner', key='banner')
        self.produk = Product.objects.create(
            nama='Spanduk Flexi',
            kategori=self.kategori,
            sku='BNR-280',
            qty_stok=0,
            lacak_inventori=True,
        )
        Supplier.objects.create(nama='Jaya Cemerlang')

    def _buat_draft(self):
        res = self.client.post(
            '/api/stock-in-documents/', {'tanggal': '2026-07-16', 'catatan': 'test'}
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return res.data['id']

    def _import(self, doc_id, berkas):
        return self.client.post(
            f'/api/stock-in-documents/{doc_id}/import-csv/',
            {'file': berkas},
            format='multipart',
        )

    def test_supplier_belum_terdaftar_ditolak(self):
        doc_id = self._buat_draft()
        res = self._import(doc_id, csv_file('Spanduk Flexi,,BNR-280,Toko Hantu,5,15000,R-01'))

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Toko Hantu', res.data['error'])
        # Ditolak di depan: tidak boleh ada item yang terlanjur dibuat.
        self.assertEqual(StockInDocument.objects.get(id=doc_id).items.count(), 0)

    def test_supplier_terdaftar_diterima(self):
        doc_id = self._buat_draft()
        res = self._import(doc_id, csv_file('Spanduk Flexi,,BNR-280,Jaya Cemerlang,5,15000,R-01'))

        # import_csv mengembalikan 201 bila ada item yang terbuat, 400 bila nol.
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data['created']), 1)

    def test_supplier_cocok_tanpa_peduli_besar_kecil_huruf(self):
        doc_id = self._buat_draft()
        res = self._import(doc_id, csv_file('Spanduk Flexi,,BNR-280,JAYA cemerlang,5,15000,R-01'))

        # import_csv mengembalikan 201 bila ada item yang terbuat, 400 bila nol.
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data['created']), 1)

    def test_supplier_kosong_tetap_boleh(self):
        # Template resmi pun mencontohkan baris tanpa supplier.
        doc_id = self._buat_draft()
        res = self._import(doc_id, csv_file('Spanduk Flexi,,BNR-280,,5,15000,R-01'))

        # import_csv mengembalikan 201 bila ada item yang terbuat, 400 bila nol.
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data['created']), 1)

    def test_melebihi_batas_baris_ditolak(self):
        doc_id = self._buat_draft()
        baris = ['Spanduk Flexi,,BNR-280,,1,15000,R-01'] * 201
        res = self._import(doc_id, csv_file(*baris))

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('200', res.data['error'])
        self.assertEqual(StockInDocument.objects.get(id=doc_id).items.count(), 0)
