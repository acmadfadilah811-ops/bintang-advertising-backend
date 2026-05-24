from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from api.models import Divisi, TahapProses, Contact, Order, OrderItem, JobBoard
from hr.models import Absensi

CustomUser = get_user_model()

# ===========================================================================
# 1. UNIT TEST MODEL-MODEL DATABASE
# ===========================================================================
class ModelsTestCase(TestCase):
    def setUp(self):
        # Setup Master Data
        self.divisi = Divisi.objects.create(nama="Desain", keterangan="Divisi Desain Grafis")
        self.tahap = TahapProses.objects.create(nama="Setting Desain", divisi=self.divisi, urutan=1)
        
        # Setup Karyawan & Pelanggan
        self.owner = CustomUser.objects.create_user(username="owner_boss", password="password123", role="owner")
        self.staff = CustomUser.objects.create_user(username="staff_karyawan", password="password123", role="staff", divisi=self.divisi)
        self.contact = Contact.objects.create(nomor_wa="08123456789", nama="John Doe")

    def test_custom_user_creation(self):
        """Uji apakah pembuatan CustomUser berhasil dengan role dan divisi yang benar."""
        self.assertEqual(self.staff.username, "staff_karyawan")
        self.assertEqual(self.staff.role, "staff")
        self.assertEqual(self.staff.divisi.nama, "Desain")
        self.assertTrue(self.staff.check_password("password123"))

    def test_order_creation_and_auto_id(self):
        """Uji pembuatan order baru dan generator auto ID (ORD-YYYYMMDD-XXXX)."""
        order = Order.objects.create(
            nomor_wa=self.contact.nomor_wa,
            nama=self.contact.nama,
            status_global="review"
        )
        self.assertIsNotNone(order.id)
        self.assertTrue(order.id.startswith("ORD-"))
        
        # Pastikan data ada di database
        db_order = Order.objects.get(pk=order.id)
        self.assertEqual(db_order.nama, "John Doe")
        self.assertEqual(db_order.status_global, "review")

    def test_order_item_and_area_calculation(self):
        """Uji area (luas) dihitung otomatis saat menyimpan OrderItem (P x L)."""
        order = Order.objects.create(nomor_wa="08123456789", nama="Jane")
        item = OrderItem.objects.create(
            order=order,
            jenis_produk="Spanduk Banner",
            panjang=3.0,
            lebar=2.0,
            harga_jual=120000,
            qty=1
        )
        # Luas = 3.0 * 2.0 = 6.0
        self.assertEqual(item.luas, 6.0)
        
        # Pastikan data tersimpan dengan benar di DB
        db_item = OrderItem.objects.get(pk=item.pk)
        self.assertEqual(db_item.luas, 6.0)

    def test_order_totals_calculation_on_item_save(self):
        """Uji apakah penambahan OrderItem secara otomatis mengupdate total_harga & sisa_tagihan pada Order."""
        order = Order.objects.create(
            nomor_wa="08123456789", 
            nama="Jane",
            dp_dibayar=50000,
            diskon_persen=10.0 # Diskon 10%
        )
        
        item = OrderItem.objects.create(
            order=order,
            jenis_produk="Spanduk Flexi",
            harga_jual=100000,
            qty=1
        )
        
        # Update database order
        order.refresh_from_db()
        
        # Subtotal = 100.000. Diskon 10% = 10.000. Total Harga = 90.000
        # DP Dibayar = 50.000. Sisa Tagihan = 90.000 - 50.000 = 40.000
        self.assertEqual(order.total_harga, 90000)
        self.assertEqual(order.sisa_tagihan, 40000)

    def test_job_board_linkage(self):
        """Uji apakah penugasan Job Board berhasil dengan status antrean default."""
        order = Order.objects.create(nomor_wa="08123456789", nama="Jane")
        item = OrderItem.objects.create(order=order, jenis_produk="Banner", harga_jual=50000)
        
        job = JobBoard.objects.create(
            order_item=item,
            tahap=self.tahap,
            pic_staff=self.staff,
            status_pekerjaan="antrean"
        )
        self.assertEqual(job.status_pekerjaan, "antrean")
        self.assertEqual(job.pic_staff.username, "staff_karyawan")
        self.assertEqual(job.tahap.nama, "Setting Desain")

    def test_absensi_durasi_kerja(self):
        """Uji kalkulasi durasi kerja pada model Absensi."""
        today = timezone.localdate()
        jam_masuk = timezone.make_aware(timezone.datetime(today.year, today.month, today.day, 8, 0, 0))
        jam_keluar = timezone.make_aware(timezone.datetime(today.year, today.month, today.day, 16, 30, 0)) # 8.5 jam
        
        absensi = Absensi.objects.create(
            staff=self.staff,
            tanggal=today,
            jam_masuk=jam_masuk,
            jam_keluar=jam_keluar,
            status="hadir"
        )
        self.assertEqual(absensi.durasi_kerja_jam, 8.5)


# ===========================================================================
# 2. INTEGRATION TEST API ENDPOINTS
# ===========================================================================
@override_settings(SECURE_SSL_REDIRECT=False)
class ApiTestCase(APITestCase):
    def setUp(self):
        self.divisi = Divisi.objects.create(nama="Cetak")
        self.owner = CustomUser.objects.create_user(username="owner_admin", password="securepassword", role="owner")
        self.staff = CustomUser.objects.create_user(username="staff_biasa", password="securepassword", role="staff")

    def test_create_user_endpoint_auth_required(self):
        """Uji endpoint auth/create-user/ wajib diautentikasi oleh Owner/Manager."""
        url = "/api/auth/create-user/"
        data = {
            "username": "karyawan_baru",
            "password": "newpassword123",
            "role": "staff"
        }
        # Tanpa login (Anonymous)
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Login sebagai Staff biasa (Forbidden)
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Login sebagai Owner (Success)
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["username"], "karyawan_baru")
        self.assertTrue(CustomUser.objects.filter(username="karyawan_baru").exists())

    def test_orders_list_and_create_api(self):
        """Uji API endpoint orders untuk menampilkan daftar pesanan dan membuat pesanan."""
        self.client.force_authenticate(user=self.owner)
        
        # 1. Test POST (Create Order)
        url = "/api/orders/"
        data = {
            "nomor_wa": "08987654321",
            "nama": "Pelanggan Baru",
            "status_global": "review"
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        order_id = response.data["id"]
        self.assertTrue(Order.objects.filter(pk=order_id).exists())

        # 2. Test GET (List Orders)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data) >= 1)
