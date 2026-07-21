from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.utils import timezone
from hr.models import Absensi, Akun, Kontrak
from api.models import Divisi, TahapProses, Order, OrderItem, JobBoard, SaldoKasHarian, RingkasanShift, Contact
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
            harga_jual_toko=10000.0,
            lacak_inventori=True
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            nama_varian="Standard 280g",
            qty_stok=5.00,
            harga_jual_toko=10000.0,
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

        # Check that the variant stock is reduced by 2 (from 5.0 to 3.0)
        self.assertEqual(float(self.variant.qty_stok), 3.00)

    def test_kasir_and_staff_cannot_export_data(self):
        """
        Memverifikasi bahwa role kasir dan staff diblokir (403 Forbidden) di seluruh endpoint export.
        """
        export_urls = [
            "/api/export/orders/",
            "/api/export/inventory/",
            "/api/export/jobs/",
            "/api/export/contacts/",
            "/api/export/customers/",
            "/api/export/absensi/",
            "/api/export/staff-performance/",
            "/api/export/stock-movement/",
            "/api/export/products/",
            "/api/export/cash-transactions/",
            "/api/export/sales-items-by-brand/",
            "/api/export/sales-details/",
        ]
        
        # Test as Kasir -> 403 Forbidden
        self.client.force_authenticate(user=self.kasir)
        for url in export_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, f"Endpoint {url} tidak diblokir untuk Kasir")
            
        # Test as Staff -> 403 Forbidden
        self.client.force_authenticate(user=self.staff)
        for url in export_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, f"Endpoint {url} tidak diblokir untuk Staff")

        # Test as Owner -> should not be 403 (e.g. 200 or 201 or spreadsheet download)
        self.client.force_authenticate(user=self.owner)
        for url in export_urls:
            response = self.client.get(url)
            self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN, f"Endpoint {url} diblokir untuk Owner")

    def test_staff_cannot_modify_restricted_settings(self):
        """
        Memverifikasi bahwa staff tidak bisa mengubah data diskon, kupon, promo POS, supplier, tipe pelanggan, shift timings, dll.
        """
        self.client.force_authenticate(user=self.staff)
        
        # 1. Sales Discount
        response = self.client.post("/api/sales-discounts/", {"nama": "Promo Merdeka"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # 2. Coupon
        response = self.client.post("/api/discount-coupons/", {"nama": "Kupon Baru"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # 3. POS Promotion
        response = self.client.post("/api/pos-promotions/", {"nama": "Promo POS"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # 4. Customer Group
        response = self.client.post("/api/customer-groups/", {"name": "Grup Baru"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # 5. Supplier
        response = self.client.post("/api/suppliers/", {"name": "Supplier Baru"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_read_contact_stats(self):
        """
        Memverifikasi bahwa staff dan kasir diblokir saat memanggil statistik pelanggan/piutang (ContactStatsView).
        """
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/contacts/stats/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        self.client.force_authenticate(user=self.kasir)
        response = self.client.get("/api/contacts/stats/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # Owner can read
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/contacts/stats/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_be24_staff_blocked_from_full_contacts_and_customers(self):
        """
        BE-24: staff TIDAK boleh membaca seluruh database pelanggan (/contacts/,
        /customers/, /customer-groups/) — baik lewat GET maupun tulis. Kebutuhan
        papan produksi dilayani endpoint sempit /contacts/production-lite/ yang
        hanya mengembalikan nama + nomor WA (tanpa piutang/total belanja).
        """
        # Staff diblokir membaca endpoint penuh.
        self.client.force_authenticate(user=self.staff)
        for endpoint in ["/api/contacts/", "/api/customers/", "/api/customer-groups/"]:
            response = self.client.get(endpoint)
            self.assertEqual(
                response.status_code, status.HTTP_403_FORBIDDEN,
                msg=f"Staff seharusnya diblokir GET {endpoint}",
            )

        # Kasir tetap bisa membaca (dibutuhkan POS).
        self.client.force_authenticate(user=self.kasir)
        self.assertEqual(self.client.get("/api/contacts/").status_code, status.HTTP_200_OK)

        # Owner tetap bisa membaca.
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.get("/api/contacts/").status_code, status.HTTP_200_OK)

    def test_be24_production_lite_endpoint_exposes_only_name_and_wa(self):
        """
        BE-24: endpoint sempit papan produksi dapat diakses staff, namun HANYA
        mengembalikan nama + nomor_wa — tidak boleh membocorkan data finansial
        (total_spent / piutang / keterangan).
        """
        Contact.objects.create(
            nomor_wa="628123456789", nama="Pelanggan Uji",
            total_order=3, total_spent=500000, keterangan="catatan rahasia",
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get("/api/contacts/production-lite/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["results"]
        self.assertGreaterEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(set(row.keys()), {"nama", "nomor_wa"})
        self.assertNotIn("total_spent", row)
        self.assertNotIn("keterangan", row)

    def test_be24_production_lite_filters_and_caps_results(self):
        """
        Endpoint sempit menyaring di server (`?search=`) dan membatasi jumlah
        baris. Tanpa batas, papan produksi akan menarik seluruh tabel kontak
        setiap kali dibuka; tanpa flag `truncated`, hasil terpotong akan
        membuat pelanggan seolah hilang dari pencarian tanpa peringatan.
        """
        from api.views.contacts import ProductionCustomerLiteView

        limit = ProductionCustomerLiteView.MAX_ROWS
        Contact.objects.bulk_create([
            Contact(nomor_wa=f"62811{i:07d}", nama=f"Pelanggan {i:04d}")
            for i in range(limit + 25)
        ])
        Contact.objects.create(nomor_wa="628999000111", nama="Budi Sablon")

        self.client.force_authenticate(user=self.staff)

        # Tanpa search: dibatasi MAX_ROWS dan ditandai terpotong.
        response = self.client.get("/api/contacts/production-lite/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), limit)
        self.assertTrue(response.data["truncated"])
        self.assertEqual(response.data["limit"], limit)

        # Dengan search: hanya yang cocok, dan tidak ditandai terpotong.
        response = self.client.get("/api/contacts/production-lite/", {"search": "Budi Sablon"})
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["nama"], "Budi Sablon")
        self.assertFalse(response.data["truncated"])

        # Pencarian juga berlaku untuk nomor WA.
        response = self.client.get("/api/contacts/production-lite/", {"search": "628999000111"})
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["nomor_wa"], "628999000111")

    def test_be10_staff_cannot_update_order_item(self):
        """
        BE-10: Staff (meskipun PIC pekerjaan) diblokir (403 Forbidden) saat melakukan
        PATCH/PUT ke /api/order-items/{id}/ untuk mengubah harga jual atau detail pesanan.
        """
        JobBoard.objects.create(
            order_item=self.order_item,
            tahap=self.tahap,
            pic_staff=self.staff
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.patch(f"/api/order-items/{self.order_item.id}/", {
            "harga_jual": 1
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_read_audit_logs_and_sessions(self):
        """
        Memverifikasi bahwa staff/kasir diblokir saat memanggil API log audit dan kelola sesi aktif.
        """
        for user in [self.staff, self.kasir]:
            self.client.force_authenticate(user=user)
            self.assertEqual(self.client.get("/api/security/audit-log/").status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.client.get("/api/security/sessions/").status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.client.delete("/api/security/sessions/1/").status_code, status.HTTP_403_FORBIDDEN)

    def test_webhook_fail_closed_without_secrets(self):
        """
        Memverifikasi bahwa webhook Evolution, Fonnte, dan error logger fail-closed (500)
        jika env var secret masing-masing tidak dikonfigurasi.
        """
        import os
        from unittest import mock
        
        # Simpan env vars asli
        orig_evo = os.getenv("EVOLUTION_API_KEY")
        orig_fonnte = os.getenv("FONNTE_WEBHOOK_SECRET")
        orig_client = os.getenv("CLIENT_LOG_SECRET")
        
        try:
            # Hapus env vars agar kosong/tidak diset
            if "EVOLUTION_API_KEY" in os.environ: del os.environ["EVOLUTION_API_KEY"]
            if "FONNTE_WEBHOOK_SECRET" in os.environ: del os.environ["FONNTE_WEBHOOK_SECRET"]
            if "CLIENT_LOG_SECRET" in os.environ: del os.environ["CLIENT_LOG_SECRET"]
            
            # 1. Evolution webhook fail closed
            response = self.client.post("/api/webhook/evolution/", {}, format="json")
            self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertIn("Evolution API key not configured", response.data["error"])
            
            # 2. Fonnte webhook fail closed
            response = self.client.post("/api/webhook/fonnte/", {}, format="json")
            self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertIn("Webhook secret not configured", response.data["error"])
            
            # 3. Client log error fail closed
            response = self.client.post("/api/log-client-error/", {}, format="json")
            self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertIn("Client log secret not configured", response.data["error"])
            
        finally:
            # Kembalikan env vars asli
            if orig_evo: os.environ["EVOLUTION_API_KEY"] = orig_evo
            if orig_fonnte: os.environ["FONNTE_WEBHOOK_SECRET"] = orig_fonnte
            if orig_client: os.environ["CLIENT_LOG_SECRET"] = orig_client

    def test_webhook_and_logger_authentication_success_and_fail(self):
        """
        Memverifikasi autentikasi webhook Evolution, Fonnte, dan error logger.
        """
        import os
        from unittest import mock
        
        with mock.patch.dict(os.environ, {
            "EVOLUTION_API_KEY": "ValidEvoKey",
            "FONNTE_WEBHOOK_SECRET": "ValidFonnteSecret",
            "CLIENT_LOG_SECRET": "ValidClientSecret"
        }):
            # Test Evolution Webhook
            # Gagal - token salah
            response = self.client.post("/api/webhook/evolution/", {}, HTTP_APIKEY="WrongKey")
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            # Sukses (tapi bad request / empty payload, yang penting lewat auth / not 401/500)
            with mock.patch("api.views.whatsapp.logger"):
                response = self.client.post("/api/webhook/evolution/", {}, HTTP_APIKEY="ValidEvoKey")
                self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertNotEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Test Fonnte Webhook
            # Gagal - token salah
            response = self.client.post("/api/webhook/fonnte/?secret=WrongSecret", {})
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            # Sukses
            response = self.client.post("/api/webhook/fonnte/?secret=ValidFonnteSecret", {})
            self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            self.assertNotEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Test Client Logger
            # Gagal - token salah
            response = self.client.post("/api/log-client-error/", {}, HTTP_X_CLIENT_LOG_AUTH="WrongSecret")
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            # Sukses
            response = self.client.post("/api/log-client-error/", {"error": "Test crash"}, HTTP_X_CLIENT_LOG_AUTH="ValidClientSecret")
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_dynamic_scoping_attendance_timecard_contracts(self):
        """
        Memverifikasi bahwa AbsensiListView, TimecardView, dan KontrakView benar-benar
        men-scope data per-user ketika diakses oleh staff biasa.
        """
        # Buat staff lain
        staff_other = User.objects.create_user(username="staff_other", password="password123", role="staff", divisi=self.divisi)
        
        # 1. Absensi data
        today = timezone.localdate()
        abs_self = Absensi.objects.create(staff=self.staff, tanggal=today, jam_masuk=timezone.now())
        abs_other = Absensi.objects.create(staff=staff_other, tanggal=today, jam_masuk=timezone.now())
        
        # 2. Kontrak data
        kontrak_self = Kontrak.objects.create(
            staff=self.staff,
            nomor_kontrak="K-1",
            tipe="tetap",
            tanggal_mulai=today,
            gaji_pokok=5000000,
            status="aktif"
        )
        kontrak_other = Kontrak.objects.create(
            staff=staff_other,
            nomor_kontrak="K-2",
            tipe="tetap",
            tanggal_mulai=today,
            gaji_pokok=6000000,
            status="aktif"
        )

        # A. UJI COBA SEBAGAI STAFF BIASA
        self.client.force_authenticate(user=self.staff)
        
        # Absensi
        response = self.client.get(f"/api/hr/absensi/?tanggal={today}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Hanya boleh melihat absensi dirinya sendiri
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["staff"], self.staff.id)
        
        # Timecard
        response = self.client.get(f"/api/hr/timecard/?bulan={today.month}&tahun={today.year}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Hanya boleh melihat timecard miliknya sendiri
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["staff_id"], self.staff.id)
        
        # Kontrak
        response = self.client.get("/api/hr/kontrak/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Hanya boleh melihat kontrak miliknya sendiri
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], kontrak_self.id)

        # B. UJI COBA SEBAGAI OWNER (Bisa lihat semua)
        self.client.force_authenticate(user=self.owner)
        
        # Absensi
        response = self.client.get(f"/api/hr/absensi/?tanggal={today}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 2)
        
        # Timecard
        response = self.client.get(f"/api/hr/timecard/?bulan={today.month}&tahun={today.year}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data) >= 2)
        
        # Kontrak
        response = self.client.get("/api/hr/kontrak/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
