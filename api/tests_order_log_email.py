"""Test OrderActivityLogSerializer mengirim email pembuat/pengubah order.

Sebelumnya serializer hanya mengirim username, jadi frontend menambal ketiadaan
email dengan alamat hardcoded. Sekarang email ikut dikirim.

user di OrderActivityLog SET_NULL — bisa kosong bila akunnya dihapus. Serializer
tidak boleh error dalam kasus itu.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from api.models import Order, OrderActivityLog
from api.serializers import OrderActivityLogSerializer

User = get_user_model()


class OrderLogEmailTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='kasir1', email='kasir1@bintang.test', password='x', role='kasir'
        )
        self.order = Order.objects.create(nomor_wa='628123', nama='Pembeli')

    def test_email_dikirim_saat_user_ada(self):
        log = OrderActivityLog.objects.create(
            order=self.order, user=self.user, tindakan='CREATE_ORDER', keterangan='dibuat',
            waktu=timezone.now(),
        )
        data = OrderActivityLogSerializer(log).data
        self.assertEqual(data['user_nama'], 'kasir1')
        self.assertEqual(data['user_email'], 'kasir1@bintang.test')

    def test_user_null_tidak_error(self):
        # Akun pembuat log terlanjur dihapus (SET_NULL).
        log = OrderActivityLog.objects.create(
            order=self.order, user=None, tindakan='UPDATE_ORDER', keterangan='diubah',
            waktu=timezone.now(),
        )
        data = OrderActivityLogSerializer(log).data
        # Harus ada key-nya (bernilai None), bukan meledak.
        self.assertIsNone(data['user_nama'])
        self.assertIsNone(data['user_email'])
