from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.utils import timezone
from hr.models import Absensi, Akun
from api.models import Divisi, TahapProses, Order, OrderItem, JobBoard, SaldoKasHarian, RingkasanShift
from api.product_models import Product, ProductVariant, ProductCategory
import datetime

User = get_user_model()

class SecurityPermissionTestCase(APITestCase):
    def setUp(self):
        # Create Divisi
        self.divisi = Divisi.objects.create(nama="Finishing")
        
        # Create TahapProses
        self.tahap = TahapProses.objects.create(nama="Finishing Mata Ayam", divisi=self.divisi, urutan=1)

        # Create Users
        self.owner = User.objects.create_user(username="owner_user", password="password123", role="owner")
        self.manager = User.objects.create_user(username="manager_user", password="password123", role="manager")
        self.admin = User.objects.create_user(username="admin_user", password="password123", role="admin")
        self.kasir = User.objects.create_user(username="kasir_user", password="password123", role="kasir")
        self.staff = User.objects.create_user(username="staff_user", password="password123", role="staff", divisi=self.divisi)

        # Create Category & Product for testing
        self.category = ProductCategory.objects.create(nama="Banner", key="banner")
        self.product = Product.objects.create(
            nama="Spanduk Flexi",
            kategori=self.category,
            qty_stok=10.00,
            lacak_inventori=True
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            nama_varian="Standard 280g",
            qty_stok=5.00,
            lacak_inventori=True
        )

        # Create Account for finance tests
        self.akun = Akun.objects.create(
            kode_akun="111",
            nama_akun="Kas Toko",
            kategori="aset"
        )

        # Create Order & OrderItem
        self.order = Order.objects.create(
            nomor_wa="08123456789",
            nama="Budi"
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            jenis_produk="Cetak Banner",
            qty=1,
            harga_jual=10000
        )

    def test_kasir_cannot_access_sensitive_endpoints(self):
        """
        Memverifikasi bahwa role kasir diblokir (403 Forbidden) di:
        - GET /api/users/ (CustomUserViewSet list)
        - GET /api/finance/akun/ (AkunViewSet)
        - GET /api/finance/buku-besar/ (BukuBesarView)
        - POST /api/products/ (ProductViewSet)
        - POST /api/hr/slip-gaji/generate/ (SlipGajiViewSet)
        """
        # Authenticate as Kasir
        self.client.force_authenticate(user=self.kasir)

        # 1. CustomUser list
        response = self.client.get("/api/users/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Akun list
        response = self.client.get("/api/finance/akun/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Buku Besar
        response = self.client.get("/api/finance/buku-besar/", {
            "akun_id": self.akun.id,
            "start_date": "2026-07-01",
            "end_date": "2026-07-31"
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Create Product
        response = self.client.post("/api/products/", {
            "nama": "Banner Roll Up",
            "kategori": self.category.id
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Generate Payroll
        response = self.client.post("/api/hr/slip-gaji/generate/", {
            "bulan": 7,
            "tahun": 2026
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_clock_in_permission(self):
        """
        Memverifikasi alur logic Clock-In pada IsClockedIn:
        - Tanpa record Absensi hari ini: 403 Forbidden.
        - Memiliki Absensi hari ini tapi jam_masuk kosong: 403 Forbidden.
        - Memiliki Absensi hari ini dengan jam_masuk: 200 OK.
        - Memiliki Absensi hari ini dengan jam_keluar (check-out) dan workspace_unlocked = False: 403 Forbidden.
        - Memiliki Absensi hari ini dengan jam_keluar (check-out) dan workspace_unlocked = True: 200 OK.
        """
        # Create a JobBoard item
        job = JobBoard.objects.create(
            order_item=self.order_item,
            tahap=self.tahap,
            pic_staff=self.staff
        )

        self.client.force_authenticate(user=self.staff)

        # 1. Tanpa record Absensi
        response = self.client.get(f"/api/jobs/{job.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Absensi ada tapi jam_masuk kosong (misal: hanya absen Alpha/Izin)
        today = timezone.localdate()
        absensi = Absensi.objects.create(staff=self.staff, tanggal=today, jam_masuk=None)
        response = self.client.get(f"/api/jobs/{job.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Absensi dengan jam_masuk (Clocked In)
        absensi.jam_masuk = timezone.now()
        absensi.save()
        response = self.client.get(f"/api/jobs/{job.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 4. Absensi dengan jam_keluar (Clocked Out / Check Out)
        absensi.jam_keluar = timezone.now()
        absensi.save()
        response = self.client.get(f"/api/jobs/{job.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Absensi dengan jam_keluar dan workspace_unlocked = True
        absensi.workspace_unlocked = True
        absensi.save()
        response = self.client.get(f"/api/jobs/{job.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_owner_manager_admin_bypass_clock_in(self):
        """
        Memverifikasi bahwa role owner, manager, dan admin dapat mengakses JobBoard
        meskipun mereka tidak memiliki record Absensi hari ini.
        """
        job = JobBoard.objects.create(
            order_item=self.order_item,
            tahap=self.tahap,
            pic_staff=self.staff
        )

        for user in [self.owner, self.manager, self.admin]:
            self.client.force_authenticate(user=user)
            response = self.client.get(f"/api/jobs/{job.id}/")
            self.assertEqual(response.status_code, status.HTTP_200_OK, f"Role {user.role} gagal mengakses job tanpa clock-in")

    def test_kasir_saldo_kas_and_shift_scoping(self):
        """
        Memverifikasi bahwa kasir hanya dapat melihat record SaldoKasHarian dan RingkasanShift
        milik mereka sendiri di get_queryset().
        """
        kasir2 = User.objects.create_user(username="kasir_user2", password="password123", role="kasir")

        # Create SaldoKasHarian records
        kas_harian1 = SaldoKasHarian.objects.create(
            kasir=self.kasir,
            tanggal=timezone.localdate(),
            shift="Pagi",
            kas_awal=50000.0
        )
        kas_harian2 = SaldoKasHarian.objects.create(
            kasir=kasir2,
            tanggal=timezone.localdate(),
            shift="Pagi",
            kas_awal=75000.0
        )

        # Create RingkasanShift records
        shift1 = RingkasanShift.objects.create(
            kasir=self.kasir,
            tanggal=timezone.localdate(),
            mulai=timezone.now()
        )
        shift2 = RingkasanShift.objects.create(
            kasir=kasir2,
            tanggal=timezone.localdate(),
            mulai=timezone.now()
        )

        # 1. Authenticate as kasir1
        self.client.force_authenticate(user=self.kasir)

        # Get Saldo Kas
        response = self.client.get("/api/saldo-kas-harian/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only return 1 record (belonging to self.kasir)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], kas_harian1.id)

        # Get Ringkasan Shift
        response = self.client.get("/api/ringkasan-shift/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only return 1 record
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], shift1.id)

        # 2. Authenticate as Owner (can see all)
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/saldo-kas-harian/")
        self.assertEqual(len(response.data), 2)

    def test_pos_reduces_stock_successfully(self):
        """
        Memverifikasi bahwa:
        1. Kasir tidak bisa memodifikasi produk via POST /api/products/ (403).
        2. Namun, kasir bisa melakukan POS sale via POST /api/pos/sales/ (201).
        3. Dan transaksi POS berhasil memotong stok produk dan varian secara otomatis.
        """
        self.client.force_authenticate(user=self.kasir)

        # Kasir try modify products directly -> 403 Forbidden
        response = self.client.post("/api/products/", {
            "nama": "Unauthorized Product Creation"
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Kasir perform checkout / sale in POS
        pos_data = {
            "subtotal": 20000.0,
            "diskon": 0.0,
            "pajak": 0.0,
            "total": 20000.0,
            "dibayar": 20000.0,
            "kembalian": 0.0,
            "metode_bayar": "Cash",
            "status": "paid",
            "items": [
                {
                    "product_id": self.product.id,
                    "variant_id": self.variant.id,
                    "qty": 2.0,
                    "nama": "Spanduk Flexi Standard 280g",
                    "harga": 10000.0
                }
            ]
        }

        # POST /api/pos/sales/ -> 201 Created
        response = self.client.post("/api/pos/sales/", pos_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Reload product and variant from DB
        self.product.refresh_from_db()
        self.variant.refresh_from_db()

        # Check that the stock is reduced by 2
        self.assertEqual(float(self.product.qty_stok), 8.00)
        self.assertEqual(float(self.variant.qty_stok), 3.00)
