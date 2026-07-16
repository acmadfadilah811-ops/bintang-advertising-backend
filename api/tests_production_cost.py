"""Test fitur Biaya Produksi.

Yang dijaga di sini:
1. Hanya manajemen yang boleh menulis; staff/kasir read-only.
2. Biaya tidak bisa ditambah/diubah/DIHAPUS pada dokumen non-draft.
   Poin 'dihapus' penting: serializer.validate() tidak dipanggil saat DELETE,
   jadi penjagaannya diulang di ViewSet.destroy().
3. total_biaya = jumlah biaya dokumen (bukan item).
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api.product_models import StockProductionDocument
from api.production_models import ProductionCost
from hr.models import Akun

User = get_user_model()


class BiayaProduksiTest(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner_bp', password='password123', role='owner'
        )
        self.staff = User.objects.create_user(
            username='staff_bp', password='password123', role='staff'
        )
        self.akun = Akun.objects.create(kode_akun='5-100', nama_akun='Beban Listrik')
        self.biaya_listrik = ProductionCost.objects.create(
            nama='Listrik', nilai=Decimal('50000'), akun=self.akun
        )
        self.client.force_authenticate(user=self.owner)

    def _buat_draft(self):
        res = self.client.post(
            '/api/stock-production-documents/', {'tanggal': '2026-07-16', 'catatan': 'test'}
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return res.data['id']

    # --- Otorisasi ---

    def test_staff_boleh_baca_tapi_tidak_boleh_menulis_master(self):
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.get('/api/production-costs/').status_code, status.HTTP_200_OK)
        res = self.client.post('/api/production-costs/', {
            'nama': 'Lembur', 'nilai': '10000', 'akun': self.akun.id,
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_akun_boleh_kosong(self):
        # Olsera melabelinya "Akun terkait (Opsional)". Memaksa akun akan
        # mengunci user yang belum menyiapkan daftar akun sama sekali.
        res = self.client.post('/api/production-costs/', {'nama': 'Lembur', 'nilai': '10000'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data['akun'])
        self.assertIsNone(res.data['akun_nama'])

    def test_master_tidak_perlu_endpoint_akun_untuk_menampilkan_nama(self):
        # Nama akun ikut di payload, jadi frontend tak perlu memanggil
        # /api/finance/akun/ (yang ditolak untuk staff/kasir).
        res = self.client.get('/api/production-costs/')
        data = res.data['results'] if isinstance(res.data, dict) else res.data
        self.assertEqual(data[0]['akun_nama'], 'Beban Listrik')
        self.assertEqual(data[0]['akun_kode'], '5-100')

    # --- Biaya per dokumen ---

    def test_tambah_biaya_ke_dokumen_draft(self):
        doc_id = self._buat_draft()
        res = self.client.post('/api/stock-production-costs/', {
            'document': doc_id, 'production_cost': self.biaya_listrik.id, 'nilai': '75000',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # nilai boleh beda dari master (50000) — biaya tiap produksi berbeda
        self.assertEqual(Decimal(res.data['nilai']), Decimal('75000'))
        self.assertEqual(res.data['production_cost_nama'], 'Listrik')

    def test_total_biaya_menjumlahkan_biaya_bukan_item(self):
        doc_id = self._buat_draft()
        biaya_lain = ProductionCost.objects.create(
            nama='Lembur', nilai=Decimal('20000'), akun=self.akun
        )
        self.client.post('/api/stock-production-costs/', {
            'document': doc_id, 'production_cost': self.biaya_listrik.id, 'nilai': '75000',
        })
        self.client.post('/api/stock-production-costs/', {
            'document': doc_id, 'production_cost': biaya_lain.id, 'nilai': '25000',
        })
        res = self.client.get(f'/api/stock-production-documents/{doc_id}/')
        self.assertEqual(Decimal(res.data['total_biaya']), Decimal('100000'))
        self.assertEqual(len(res.data['biaya']), 2)

    def test_biaya_sama_tidak_bisa_ditambahkan_dua_kali(self):
        doc_id = self._buat_draft()
        payload = {'document': doc_id, 'production_cost': self.biaya_listrik.id, 'nilai': '75000'}
        self.assertEqual(self.client.post('/api/stock-production-costs/', payload).status_code,
                         status.HTTP_201_CREATED)
        # Ubah nilainya, jangan tambah baris kedua.
        self.assertEqual(self.client.post('/api/stock-production-costs/', payload).status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_nilai_negatif_ditolak(self):
        doc_id = self._buat_draft()
        res = self.client.post('/api/stock-production-costs/', {
            'document': doc_id, 'production_cost': self.biaya_listrik.id, 'nilai': '-5000',
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Penjagaan draft-only ---

    def test_tidak_bisa_menambah_biaya_ke_dokumen_non_draft(self):
        doc_id = self._buat_draft()
        StockProductionDocument.objects.filter(id=doc_id).update(status='selesai')
        res = self.client.post('/api/stock-production-costs/', {
            'document': doc_id, 'production_cost': self.biaya_listrik.id, 'nilai': '75000',
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tidak_bisa_menghapus_biaya_dari_dokumen_non_draft(self):
        # serializer.validate() TIDAK jalan saat DELETE — tanpa guard di
        # ViewSet.destroy(), biaya dokumen terposting bisa dihapus diam-diam.
        doc_id = self._buat_draft()
        res = self.client.post('/api/stock-production-costs/', {
            'document': doc_id, 'production_cost': self.biaya_listrik.id, 'nilai': '75000',
        })
        biaya_id = res.data['id']
        StockProductionDocument.objects.filter(id=doc_id).update(status='selesai')

        res = self.client.delete(f'/api/stock-production-costs/{biaya_id}/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
