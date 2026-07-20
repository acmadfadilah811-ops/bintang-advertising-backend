from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .product_models import Product, ProductPackage, SpecialType


class ProductSpecialTypeDanBarcodeTest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='owner', password='x', role='owner')
        self.client.force_authenticate(self.user)
        self.unggulan = SpecialType.objects.get(key='unggulan')
        self.sale = SpecialType.objects.get(key='sale')
        self.product = Product.objects.create(nama='Kaos Polos', harga_jual_toko=50000)
        self.product.tipe_specials.set([self.unggulan, self.sale])

    def test_duplikat_mempertahankan_tipe_specials(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/copy/', {'nama': 'Kaos Polos Copy'}, format='json'
        )
        self.assertIn(res.status_code, (200, 201), res.content)
        salinan = Product.objects.get(nama='Kaos Polos Copy')
        self.assertEqual(
            sorted(salinan.tipe_specials.values_list('key', flat=True)), ['sale', 'unggulan']
        )

    def test_dua_paket_tanpa_sku_tidak_bentrok(self):
        for nama in ('Paket A', 'Paket B'):
            res = self.client.post(
                '/api/product-packages/',
                {'nama': nama, 'sku': '', 'barcode': '', 'harga_jual_online': 1000},
                format='multipart',
            )
            self.assertIn(res.status_code, (200, 201), res.content)
        self.assertEqual(ProductPackage.objects.filter(sku__isnull=True).count(), 2)
