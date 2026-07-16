from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework import status
from api.marketing_models import POSPromotion
import importlib
migration_module = importlib.import_module('api.migrations.0064_pospromotion_produk_qty')
migrate_produk_to_qty = migration_module.migrate_produk_to_qty
migrate_qty_to_produk = migration_module.migrate_qty_to_produk

CustomUser = get_user_model()


class POSPromotionQtyTestCase(APITestCase):
    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            username="owner_admin", password="securepassword", role="owner"
        )
        self.client.force_authenticate(user=self.owner)

    def test_create_promo_with_valid_qty(self):
        """Uji pembuatan promosi POS dengan produk_qty yang valid."""
        url = "/api/pos-promotions/"
        data = {
            "judul": "Promo Mantap",
            "tipe_promosi": "BX",
            "produk_qty": [
                {"nama": "Spanduk Flexi", "qty": 2},
                {"nama": "Banner Roll Up", "qty": 1}
            ],
            "tanggal_aktif": "2026-07-17",
            "tanpa_kadaluarsa": True
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["produk_qty"], [
            {"nama": "Spanduk Flexi", "qty": 2},
            {"nama": "Banner Roll Up", "qty": 1}
        ])

    def test_create_promo_qty_default_fallback(self):
        """Uji fallback default qty ke 1 jika field qty dilewati."""
        url = "/api/pos-promotions/"
        data = {
            "judul": "Promo Default Qty",
            "tipe_promosi": "BX",
            "produk_qty": [
                {"nama": "Sticker Graftac"}
            ],
            "tanggal_aktif": "2026-07-17",
            "tanpa_kadaluarsa": True
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["produk_qty"], [
            {"nama": "Sticker Graftac", "qty": 1}
        ])

    def test_create_promo_qty_validation_zero_negative(self):
        """Uji validasi error jika qty kurang dari 1."""
        url = "/api/pos-promotions/"
        
        # Zero
        data = {
            "judul": "Promo Salah",
            "tipe_promosi": "BX",
            "produk_qty": [{"nama": "Spanduk", "qty": 0}],
            "tanggal_aktif": "2026-07-17",
            "tanpa_kadaluarsa": True
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("produk_qty", response.data)

        # Negative
        data["produk_qty"] = [{"nama": "Spanduk", "qty": -5}]
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_promo_qty_invalid_type(self):
        """Uji validasi error jika tipe data qty bukan integer dan tidak bisa di-cast."""
        url = "/api/pos-promotions/"
        data = {
            "judul": "Promo Salah",
            "tipe_promosi": "BX",
            "produk_qty": [{"nama": "Spanduk", "qty": "not-a-number"}],
            "tanggal_aktif": "2026-07-17",
            "tanpa_kadaluarsa": True
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class POSPromotionMigrationTestCase(TestCase):
    def test_migration_forward_and_backward(self):
        """Uji fungsi migrasi forward (String -> JSON) dan backward (JSON -> String)."""
        # Mock class/objects since we want to test mapping logic
        class MockPromo:
            def __init__(self, produk="", produk_qty=None):
                self.produk = produk
                self.produk_qty = produk_qty if produk_qty is not None else []
            def save(self):
                pass

        class MockPOSPromotionQuerySet:
            def __init__(self, items):
                self.items = items
            def all(self):
                return self.items

        class MockPOSPromotionModel:
            objects = None

        class MockApps:
            def get_model(self, app_label, model_name):
                return MockPOSPromotionModel

        # 1. Forward Migration Test
        items = [
            MockPromo(produk="Spanduk Flexi, Banner Roll Up"),
            MockPromo(produk=""),
            MockPromo(produk="  Single Product  ")
        ]
        MockPOSPromotionModel.objects = MockPOSPromotionQuerySet(items)

        migrate_produk_to_qty(MockApps(), None)

        self.assertEqual(items[0].produk_qty, [
            {"nama": "Spanduk Flexi", "qty": 1},
            {"nama": "Banner Roll Up", "qty": 1}
        ])
        self.assertEqual(items[1].produk_qty, [])
        self.assertEqual(items[2].produk_qty, [
            {"nama": "Single Product", "qty": 1}
        ])

        # 2. Backward Migration Test
        items_back = [
            MockPromo(produk_qty=[{"nama": "A", "qty": 2}, {"nama": "B", "qty": 1}]),
            MockPromo(produk_qty=[]),
            MockPromo(produk_qty=[{"nama": "C", "qty": 1}])
        ]
        MockPOSPromotionModel.objects = MockPOSPromotionQuerySet(items_back)

        migrate_qty_to_produk(MockApps(), None)

        self.assertEqual(items_back[0].produk, "A, B")
        self.assertEqual(items_back[1].produk, "")
        self.assertEqual(items_back[2].produk, "C")
