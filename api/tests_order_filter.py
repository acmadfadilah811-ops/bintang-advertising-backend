"""Filter daftar pesanan dan cakupan akses kasir.

Menjaga dua hal yang sebelumnya rusak diam-diam:
  - ?status_global= dan ?sumber= benar-benar menyaring (dulu diabaikan,
    sehingga antrean WA dan badge kasir menghitung SELURUH order);
  - kasir melihat order (dulu jatuh ke cabang pic_staff dan menerima kosong).
"""

from django.urls import reverse
from rest_framework.test import APITestCase

from .models import CustomUser, Divisi, JobBoard, Order, OrderItem, TahapProses


class OrderFilterTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kasir = CustomUser.objects.create_user(
            username='kasir1', password='rahasia123', role='kasir'
        )
        cls.staff = CustomUser.objects.create_user(
            username='staff1', password='rahasia123', role='staff'
        )

        Order.objects.create(
            id='ORD-READY-1', nama='Budi', nomor_wa='628111',
            status_global='ready', sumber='manual',
        )
        Order.objects.create(
            id='ORD-PROSES-1', nama='Andi', nomor_wa='628222',
            status_global='proses', sumber='manual',
        )
        Order.objects.create(
            id='ORD-WA-1', nama='Sari', nomor_wa='628333',
            status_global='review', sumber='wa',
        )

    def _list(self, params):
        self.client.force_authenticate(user=self.kasir)
        return self.client.get(reverse('order-list'), params)

    def test_kasir_melihat_semua_order(self):
        res = self._list({})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 3)

    def test_filter_status_global(self):
        res = self._list({'status_global': 'ready'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([o['id'] for o in res.data], ['ORD-READY-1'])

    def test_filter_status_dan_sumber_bersamaan(self):
        res = self._list({'status_global': 'review', 'sumber': 'wa'})
        self.assertEqual([o['id'] for o in res.data], ['ORD-WA-1'])

    def test_status_tidak_valid_diabaikan(self):
        """Nilai ngawur tidak boleh menyaring apa pun, juga tidak boleh error."""
        res = self._list({'status_global': 'bukan-status'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 3)

    def test_staff_produksi_tetap_terbatas(self):
        """Pelonggaran untuk kasir tidak boleh ikut melonggarkan staff."""
        self.client.force_authenticate(user=self.staff)
        res = self.client.get(reverse('order-list'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 0)


class KasirAntreanWaTest(APITestCase):
    """Alur lengkap kasir memproses pesanan WA yang masuk otomatis.

    Bukan sekadar bisa melihat: kasir harus bisa membaca antrean, menyunting
    pesanan, lalu menerbitkan SPK ke divisi. Tiap langkah pernah gagal karena
    alasan berbeda, jadi ketiganya dikunci di sini.
    """

    @classmethod
    def setUpTestData(cls):
        cls.kasir = CustomUser.objects.create_user(
            username='kasir2', password='rahasia123', role='kasir'
        )
        cls.staff = CustomUser.objects.create_user(
            username='staff2', password='rahasia123', role='staff'
        )
        cls.divisi = Divisi.objects.create(nama='Cetak')
        cls.tahap = TahapProses.objects.create(nama='Cetak Awal', divisi=cls.divisi, urutan=1)

        cls.order = Order.objects.create(
            id='ORD-WA-PROSES', nama='Sari', nomor_wa='628444',
            status_global='review', sumber='wa',
        )
        cls.item = OrderItem.objects.create(
            order=cls.order, jenis_produk='Banner 3x2', qty=1, harga_jual=150000,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.kasir)

    def test_kasir_melihat_antrean_wa(self):
        res = self.client.get(reverse('order-list'), {'status_global': 'review', 'sumber': 'wa'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([o['id'] for o in res.data], ['ORD-WA-PROSES'])

    def test_kasir_menyunting_pesanan(self):
        res = self.client.patch(
            reverse('order-detail', args=[self.order.id]),
            {'nama': 'Sari Dewi'},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.nama, 'Sari Dewi')

    def test_kasir_menerbitkan_spk_ke_divisi(self):
        res = self.client.post(
            reverse('assign_order', args=[self.order.id]),
            {'divisi_id': self.divisi.id, 'status_global': 'proses'},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        job = JobBoard.objects.get(order_item=self.item)
        self.assertEqual(job.tahap, self.tahap)
        self.assertIsNone(job.pic_staff, 'SPK dari kasir tidak boleh punya PIC staff.')

    def test_kasir_ditolak_menunjuk_staff(self):
        res = self.client.post(
            reverse('assign_order', args=[self.order.id]),
            {'staff_id': self.staff.id},
            format='json',
        )
        self.assertEqual(res.status_code, 403)
        self.assertFalse(JobBoard.objects.filter(order_item=self.item).exists())
