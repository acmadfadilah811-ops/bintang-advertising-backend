"""Test alasan Stok Keluar.

Daftar alasannya mengikuti Olsera: Rusak, Kadaluarsa, Pengembalian dana (refund),
Jumlah stock kelebihan, Alasan lainnya. Yang terakhir disertai teks bebas.

Aturan yang dijaga di sini:
1. 'lainnya' tanpa teks ditolak — alasan yang tidak menjelaskan apa pun.
2. Teks bebas dibuang begitu alasannya bukan 'lainnya' lagi, supaya tidak ada
   dokumen ber-alasan 'Rusak' yang diam-diam masih menyimpan keterangan lama.
3. 'manual' & 'transfer' tetap sah — dipakai dokumen lama & alur transfer.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api.product_models import StockOutDocument

User = get_user_model()


class StockOutAlasanTest(APITestCase):
    def setUp(self):
        # StockOutDocumentViewSet memakai IsOwnerManagerAdminOrReadOnly.
        self.owner = User.objects.create_user(
            username='owner_alasan', password='password123', role='owner'
        )
        self.client.force_authenticate(user=self.owner)

    def _buat(self, **extra):
        payload = {'tanggal': '2026-07-16', 'catatan': 'test'}
        payload.update(extra)
        return self.client.post('/api/stock-out-documents/', payload)

    def test_alasan_olsera_diterima(self):
        for alasan in ['rusak', 'kadaluarsa', 'refund', 'kelebihan_stok']:
            res = self._buat(alasan=alasan)
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, alasan)
            self.assertEqual(res.data['alasan'], alasan)

    def test_lainnya_tanpa_teks_ditolak(self):
        res = self._buat(alasan='lainnya')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('alasan_lainnya', res.data)
        self.assertEqual(StockOutDocument.objects.count(), 0)

    def test_lainnya_dengan_teks_diterima(self):
        res = self._buat(alasan='lainnya', alasan_lainnya='Dipinjam untuk pameran')

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['alasan_lainnya'], 'Dipinjam untuk pameran')

    def test_lainnya_dengan_spasi_saja_ditolak(self):
        res = self._buat(alasan='lainnya', alasan_lainnya='   ')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('alasan_lainnya', res.data)

    def test_teks_dibuang_saat_alasan_bukan_lainnya(self):
        res = self._buat(alasan='rusak', alasan_lainnya='sisa ketikan sebelumnya')

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['alasan_lainnya'], '')

    def test_ganti_dari_lainnya_ke_rusak_ikut_membuang_teksnya(self):
        dibuat = self._buat(alasan='lainnya', alasan_lainnya='Dipinjam untuk pameran')
        doc_id = dibuat.data['id']

        res = self.client.patch(f'/api/stock-out-documents/{doc_id}/', {'alasan': 'rusak'})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['alasan_lainnya'], '')
        self.assertEqual(StockOutDocument.objects.get(id=doc_id).alasan_lainnya, '')

    def test_patch_alasan_saja_pada_dokumen_lainnya_tidak_menghapus_teksnya(self):
        # PATCH parsial: 'alasan_lainnya' tidak ikut dikirim, jadi validator harus
        # membaca nilai tersimpan — bukan menganggapnya kosong lalu menolak.
        dibuat = self._buat(alasan='lainnya', alasan_lainnya='Dipinjam untuk pameran')
        doc_id = dibuat.data['id']

        res = self.client.patch(f'/api/stock-out-documents/{doc_id}/', {'catatan': 'diubah'})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['alasan_lainnya'], 'Dipinjam untuk pameran')

    def test_manual_dan_transfer_tetap_sah(self):
        # Dipakai 8 dokumen lama di DB dev; 'transfer' juga dipasang otomatis
        # oleh import CSV saat kolom to_store_url_id terisi.
        for alasan in ['manual', 'transfer']:
            res = self._buat(alasan=alasan)
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, alasan)
