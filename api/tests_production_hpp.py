"""Penyerapan biaya produksi ke HPP (opt-in per dokumen).

Fokus: lapisan FIFO yang terbentuk saat posting, karena di situlah HPP
barang jadi sebenarnya ditentukan.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .product_models import Product, StockLayer, StockProductionDocument, StockProductionDocumentItem
from .production_models import ProductionCost, StockProductionDocumentCost


class SerapBiayaKeHppTest(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='owner', password='x', role='owner')
        self.client.force_authenticate(self.user)
        self.listrik = ProductionCost.objects.create(nama='Listrik', nilai=Decimal('1000'))

    def _dokumen(self, serap, items, biaya=None):
        doc = StockProductionDocument.objects.create(
            nomor=f'PR-{serap}-{len(items)}-{id(items)}', tanggal=date.today(),
            serap_biaya_ke_hpp=serap,
        )
        for harga_beli, qty in items:
            produk = Product.objects.create(
                nama=f'P{harga_beli}-{qty}-{id(items)}', harga_beli=Decimal(harga_beli),
                harga_jual_toko=Decimal('9999'),
            )
            StockProductionDocumentItem.objects.create(document=doc, product=produk, qty=Decimal(qty))
        if biaya is not None:
            StockProductionDocumentCost.objects.create(
                document=doc, production_cost=self.listrik, nilai=Decimal(biaya)
            )
        return doc

    def _post(self, doc):
        res = self.client.post(f'/api/stock-production-documents/{doc.id}/post-document/')
        self.assertEqual(res.status_code, 200, res.content)
        return list(StockLayer.objects.filter(sumber_nomor=doc.nomor).order_by('id'))

    def test_mati_maka_hpp_tetap_harga_beli(self):
        doc = self._dokumen(False, [('100', '10')], biaya='1000')
        layers = self._post(doc)
        self.assertEqual(layers[0].harga_beli, Decimal('100.00'))

    def test_aktif_menambah_biaya_ke_hpp(self):
        # Satu item: seluruh 1000 diserap 10 unit -> +100/unit.
        doc = self._dokumen(True, [('100', '10')], biaya='1000')
        layers = self._post(doc)
        self.assertEqual(layers[0].harga_beli, Decimal('200.00'))

    def test_alokasi_proporsional_terhadap_nilai_bahan(self):
        # Bobot: 100x10=1000 dan 300x10=3000 -> total 4000.
        # Porsi: 1000x(1000/4000)=250 -> 25/unit ; 1000x(3000/4000)=750 -> 75/unit.
        doc = self._dokumen(True, [('100', '10'), ('300', '10')], biaya='1000')
        layers = self._post(doc)
        self.assertEqual(layers[0].harga_beli, Decimal('125.00'))
        self.assertEqual(layers[1].harga_beli, Decimal('375.00'))

    def test_bahan_nol_jatuh_ke_rata_per_unit(self):
        # Tanpa nilai bahan tidak ada dasar proporsi; biaya tetap harus terserap.
        doc = self._dokumen(True, [('0', '4'), ('0', '4')], biaya='800')
        layers = self._post(doc)
        self.assertEqual(layers[0].harga_beli, Decimal('100.00'))
        self.assertEqual(layers[1].harga_beli, Decimal('100.00'))

    def test_aktif_tanpa_biaya_tidak_mengubah_hpp(self):
        doc = self._dokumen(True, [('100', '10')], biaya=None)
        layers = self._post(doc)
        self.assertEqual(layers[0].harga_beli, Decimal('100.00'))
