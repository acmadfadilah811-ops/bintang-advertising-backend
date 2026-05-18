from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
import uuid

# ---------------------------------------------------------
# 1. MASTER DATA: DIVISI
# ---------------------------------------------------------
class Divisi(models.Model):
    nama = models.CharField(max_length=100, unique=True) # Misal: Desain, Cetak, Finishing, Pemasangan
    keterangan = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.nama

# ---------------------------------------------------------
# 2. MASTER DATA: TAHAPAN PROSES (DINAMIS & FLEKSIBEL)
# ---------------------------------------------------------
class TahapProses(models.Model):
    nama = models.CharField(max_length=100, unique=True) # Misal: Setting Desain, Cetak Spanduk, Finishing Mata Ayam
    divisi = models.ForeignKey(Divisi, on_delete=models.CASCADE, related_name='tahapan')
    urutan = models.IntegerField(default=1, help_text="Urutan jalannya proses (angka kecil didahului, misal 1: Desain, 2: Cetak)")

    class Meta:
        ordering = ['urutan']

    def __str__(self):
        return f"{self.urutan}. {self.nama} ({self.divisi.nama})"

# ---------------------------------------------------------
# 3. AKUN & AUTHENTIKASI (OWNER, MANAGER, STAFF)
# ---------------------------------------------------------
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('owner', 'Owner / Boss'),
        ('manager', 'Manager'),
        ('staff', 'Staff Produksi'),
    )
    
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    divisi = models.ForeignKey(Divisi, on_delete=models.SET_NULL, null=True, blank=True, related_name='users') 
    no_hp = models.CharField(max_length=20, null=True, blank=True)
    kota = models.CharField(max_length=50, null=True, blank=True)
    negara = models.CharField(max_length=50, default='Indonesia')
    alamat = models.TextField(null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    foto_profil = models.ImageField(upload_to='avatars/', null=True, blank=True, help_text="Foto Profil Lokal")

    def __str__(self):
        divisi_nama = self.divisi.nama if self.divisi else "Tanpa Divisi"
        return f"{self.username} ({self.get_role_display()} - {divisi_nama})"

# ---------------------------------------------------------
# 4. KONTAK & PELANGGAN
# ---------------------------------------------------------
class Contact(models.Model):
    nomor_wa = models.CharField(max_length=20, primary_key=True)
    nama = models.CharField(max_length=100)
    total_order = models.IntegerField(default=0)
    total_spent = models.IntegerField(default=0)
    last_order = models.CharField(max_length=50, default="-")
    keterangan = models.TextField(null=True, blank=True, help_text="Catatan/keterangan tentang pelanggan ini")

    def __str__(self):
        return self.nama

# ---------------------------------------------------------
# 5. MANAJEMEN PESANAN (INDUK / INVOICE)
# ---------------------------------------------------------
class Order(models.Model):
    STATUS_GLOBAL_CHOICES = (
        ('review', 'Menunggu Review Manager'),
        ('proses', 'Dalam Proses Produksi'),
        ('selesai', 'Selesai Seluruhnya'),
        ('batal', 'Dibatalkan / Cancel'),
    )

    id = models.CharField(max_length=50, primary_key=True) 
    waktu = models.DateTimeField(auto_now_add=True)
    nomor_wa = models.CharField(max_length=20)
    nama = models.CharField(max_length=100)
    status_global = models.CharField(max_length=30, choices=STATUS_GLOBAL_CHOICES, default='review')
    catatan_pelanggan = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.id} - {self.nama}"

# ---------------------------------------------------------
# 6. DETAIL ITEM PESANAN (MENDUKUNG 1 ID NOTA BANYAK ITEM)
# ---------------------------------------------------------
class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    jenis_produk = models.CharField(max_length=100) 
    
    # Format Tabel Excel disimpan di sini dalam bentuk JSON
    detail = models.JSONField(default=list, null=True, blank=True, help_text="Spesifikasi awal dari customer dalam format Tabel/JSON") 
    
    qty = models.IntegerField(default=1)
    harga_jual = models.IntegerField(default=0)
    biaya_bahan = models.IntegerField(default=0)
    estimasi = models.CharField(max_length=50, default="-")
    gdrive_customer_link = models.URLField(max_length=500, null=True, blank=True)

    def __str__(self):
        return f"{self.order.id} | {self.jenis_produk}"

# ---------------------------------------------------------
# 7. PAPAN KERJA (JOB BOARD / REKAP DATA & KINERJA STAFF)
# ---------------------------------------------------------
class JobBoard(models.Model):
    STATUS_JOB_CHOICES = (
        ('antrean', 'Dalam Antrean'),
        ('dikerjakan', 'Sedang Dikerjakan'),
        ('selesai', 'Selesai Sukses'),
        ('gagal', 'Gagal Produksi / Rusak'),      
        ('batal', 'Dibatalkan di Tengah Jalan'), 
        ('kendala', 'Ada Kendala / Pending'),
    )

    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='jobs')
    tahap = models.ForeignKey(TahapProses, on_delete=models.SET_NULL, null=True, related_name='jobs')
    pic_staff = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, limit_choices_to={'role': 'staff'}, related_name='my_tasks')
    status_pekerjaan = models.CharField(max_length=20, choices=STATUS_JOB_CHOICES, default='antrean') 
    
    # Catatan hasil modifikasi/interview staff berbentuk tabel Excel (JSON)
    catatan_staff = models.JSONField(default=list, null=True, blank=True, help_text="Keterangan staff berformat Tabel/Excel (JSON)")
    gdrive_output_link = models.URLField(max_length=500, null=True, blank=True, help_text="Link file hasil kerja staff di Google Drive")
    
    # Nominal insentif ditentukan secara manual oleh Manager
    insentif = models.IntegerField(default=0, help_text="Insentif yang ditentukan oleh Manager untuk tugas ini")
    
    waktu_mulai = models.DateTimeField(null=True, blank=True)
    waktu_selesai = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        tahap_nama = self.tahap.nama if self.tahap else "Tanpa Tahap"
        pic_nama = self.pic_staff.username if self.pic_staff else "Belum Ada PIC"
        return f"{self.order_item.jenis_produk} - Tahap {tahap_nama} [{pic_nama}]"

# ---------------------------------------------------------
# 8. INVENTORI & DATA PENDUKUNG
# ---------------------------------------------------------
def _generate_inv_id():
    """Fungsi default untuk auto-generate ID barang: INV-YYYYMMDD-XXXX"""
    from django.utils import timezone
    today    = timezone.now().strftime('%Y%m%d')
    short_id = uuid.uuid4().hex[:4].upper()
    return f'INV-{today}-{short_id}'

class InventoryItem(models.Model):
    id            = models.CharField(max_length=50, primary_key=True, default=_generate_inv_id)
    nama          = models.CharField(max_length=100)
    stok          = models.FloatField(default=0.0)
    satuan        = models.CharField(max_length=20)
    kategori      = models.CharField(max_length=50)
    min_stok      = models.FloatField(default=0.0)
    cost_per_unit = models.FloatField(default=0.0)
    supplier      = models.CharField(max_length=100, default='Unknown')

    def __str__(self):
        return self.nama

class RestockHistory(models.Model):
    """Riwayat penambahan/pengurangan stok beserta keterangan."""
    item       = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='history')
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='restock_history')
    delta      = models.FloatField(help_text='Positif = tambah, Negatif = kurangi')
    stok_awal  = models.FloatField(default=0.0)
    stok_akhir = models.FloatField(default=0.0)
    keterangan = models.TextField(blank=True, default='')
    waktu      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-waktu']

    def __str__(self):
        return f"{self.item.nama} {'+' if self.delta >= 0 else ''}{self.delta} ({self.waktu:%Y-%m-%d})"


class ProductPrice(models.Model):
    kategori = models.CharField(max_length=50)
    nama_produk = models.CharField(max_length=100)
    harga = models.IntegerField()

    def __str__(self):
        return f"{self.nama_produk} - Rp{self.harga}"

class AppConfig(models.Model):
    key = models.CharField(max_length=50, primary_key=True)
    value = models.TextField()

    def __str__(self):
        return self.key

class FAQ(models.Model):
    pertanyaan = models.CharField(max_length=200, primary_key=True)
    jawaban = models.TextField()

    def __str__(self):
        return self.pertanyaan