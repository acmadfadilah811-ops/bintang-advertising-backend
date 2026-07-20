"""Penerbitan SPK dari transaksi POS.

Fokus: SPK dari POS setara dengan SPK dari order, papan produksi tetap bisa
membacanya, dan satu SPK tidak bisa punya dua sumber sekaligus.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase

from .models import Divisi, JobBoard, Order, OrderItem, TahapProses
from .pos_models import POSSale, POSSaleItem
from .product_models import Product


class SpkDariPosTest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='owner', password='x', role='owner')
        self.divisi = Divisi.objects.create(nama='Produksi')
        self.tahap = TahapProses.objects.create(nama='Cetak', divisi=self.divisi, urutan=1)
        self.produk = Product.objects.create(nama='Spanduk', harga_beli=10000, harga_jual_toko=50000)
        self.sale = POSSale.objects.create(nomor='POS-0001', total=Decimal('50000'), status='paid')
        self.item = POSSaleItem.objects.create(
            sale=self.sale, product=self.produk, nama_snapshot='Spanduk 2x1',
            harga_snapshot=Decimal('50000'), qty=Decimal('1'), subtotal=Decimal('50000'),
        )
        self.client.force_authenticate(self.owner)

    def _terbitkan(self, **payload):
        return self.client.post(f'/api/pos/sales/{self.sale.id}/terbitkan-spk/', payload, format='json')

    def test_terbitkan_ke_divisi_membuat_job(self):
        res = self._terbitkan(divisi_id=self.divisi.id)
        self.assertEqual(res.status_code, 200, res.content)
        job = JobBoard.objects.get(pos_sale_item=self.item)
        self.assertEqual(job.tahap, self.tahap)          # tahap pertama divisi
        self.assertEqual(job.status_pekerjaan, 'antrean')
        self.assertIsNone(job.order_item)

    def test_properti_seragam_untuk_papan_produksi(self):
        self._terbitkan(divisi_id=self.divisi.id)
        job = JobBoard.objects.get(pos_sale_item=self.item)
        self.assertEqual(job.sumber, 'pos')
        self.assertEqual(job.nama_produk, 'Spanduk 2x1')
        self.assertEqual(job.nomor_sumber, 'POS-0001')

    def test_transaksi_belum_lunas_ditolak(self):
        self.sale.status = 'hold'
        self.sale.save()
        res = self._terbitkan(divisi_id=self.divisi.id)
        self.assertEqual(res.status_code, 400)
        self.assertIn('lunas', res.json()['error'])

    def test_tanpa_divisi_maupun_staff_ditolak(self):
        res = self._terbitkan()
        self.assertEqual(res.status_code, 400)
        self.assertIn('wajib diisi', res.json()['error'])

    def test_terbit_ulang_tahap_sama_tidak_menggandakan(self):
        self._terbitkan(divisi_id=self.divisi.id)
        self._terbitkan(divisi_id=self.divisi.id)
        self.assertEqual(JobBoard.objects.filter(pos_sale_item=self.item).count(), 1)

    def test_dua_sumber_sekaligus_ditolak_database(self):
        order = Order.objects.create(nama='Budi', total_harga=1000)
        order_item = OrderItem.objects.create(order=order, jenis_produk='Banner', qty=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JobBoard.objects.create(order_item=order_item, pos_sale_item=self.item, tahap=self.tahap)

    def test_tanpa_sumber_sama_sekali_ditolak_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JobBoard.objects.create(tahap=self.tahap)

    def test_order_item_detail_disintesis_untuk_papan_produksi(self):
        """Papan produksi membaca order_item_detail; job POS harus ikut terisi."""
        self._terbitkan(divisi_id=self.divisi.id)
        job = JobBoard.objects.get(pos_sale_item=self.item)
        res = self.client.get(f'/api/jobs/{job.id}/')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        detail = data['order_item_detail']
        self.assertEqual(detail['jenis_produk'], 'Spanduk 2x1')
        self.assertEqual(detail['sumber'], 'pos')
        self.assertEqual(detail['nomor_nota'], 'POS-0001')
        self.assertEqual(data['nama_produk'], 'Spanduk 2x1')
        self.assertIsNone(data['order_id'])

    def test_job_dari_order_tetap_utuh(self):
        """Regresi: alur lama tidak boleh berubah bentuk responsnya."""
        order = Order.objects.create(nama='Budi', total_harga=1000)
        order_item = OrderItem.objects.create(order=order, jenis_produk='Banner', qty=1)
        job = JobBoard.objects.create(order_item=order_item, tahap=self.tahap)
        data = self.client.get(f'/api/jobs/{job.id}/').json()
        self.assertEqual(data['order_item_detail']['jenis_produk'], 'Banner')
        self.assertEqual(data['order_id'], order.id)
        self.assertEqual(data['pelanggan_nama'], 'Budi')
        self.assertEqual(data['sumber'], 'order')
