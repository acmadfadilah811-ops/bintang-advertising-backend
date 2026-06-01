from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.utils import timezone
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
        indexes = [
            # Query: tahapan milik suatu divisi diurutkan
            models.Index(fields=['divisi', 'urutan'], name='idx_tahap_divisi_urutan'),
        ]

    def __str__(self):
        return f"{self.urutan}. {self.nama} ({self.divisi.nama})"

# ---------------------------------------------------------
# 3. AKUN & AUTHENTIKASI (OWNER, MANAGER, STAFF)
# ---------------------------------------------------------
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('owner', 'Owner / Boss'),
        ('manager', 'Manager'),
        ('admin', 'Admin'),
        ('staff', 'Staff Produksi'),
    )
    
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff', db_index=True)
    divisi = models.ForeignKey(Divisi, on_delete=models.SET_NULL, null=True, blank=True, related_name='users') 
    no_hp = models.CharField(max_length=20, null=True, blank=True)
    kota = models.CharField(max_length=50, null=True, blank=True)
    negara = models.CharField(max_length=50, default='Indonesia')
    alamat = models.TextField(null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    foto_profil = models.ImageField(upload_to='avatars/', null=True, blank=True, help_text="Foto Profil Lokal")
    
    # TAMBAHAN FIELD HR / KEPEGAWAIAN
    status_karyawan = models.CharField(max_length=20, default='aktif', help_text="aktif, cuti, nonaktif")
    jenis_kontrak = models.CharField(max_length=20, default='tetap', help_text="tetap, kontrak, magang, freelance")
    kontrak_mulai = models.DateField(null=True, blank=True)
    kontrak_selesai = models.DateField(null=True, blank=True)
    no_kpj = models.CharField(max_length=50, null=True, blank=True, help_text="Nomor Kartu Peserta Jamsostek / BPJS Ketenagakerjaan")
    bpjs_kes = models.CharField(max_length=50, null=True, blank=True, help_text="Nomor BPJS Kesehatan")
    file_pkwt = models.FileField(upload_to='dokumen_hr/pkwt/', null=True, blank=True, help_text="File Kontrak PKWT")
    nip = models.CharField(
        max_length=50, 
        unique=True, 
        null=True, 
        blank=True, 
        help_text="Nomor Induk Pegawai / Staff ID (contoh: STF-2026-001)"
    )

    def save(self, *args, **kwargs):
        if self.role == 'staff' and not self.nip:
            from django.db import transaction
            with transaction.atomic():
                current_year = timezone.now().year
                last_staff = CustomUser.objects.select_for_update().filter(
                    role='staff',
                    nip__startswith=f"STF-{current_year}-"
                ).order_by('-nip').first()
                
                next_num = 1
                if last_staff and last_staff.nip:
                    try:
                        last_num = int(last_staff.nip.split('-')[-1])
                        next_num = last_num + 1
                    except ValueError:
                        pass
                self.nip = f"STF-{current_year}-{next_num:03d}"
        super().save(*args, **kwargs)

    def __str__(self):
        divisi_nama = self.divisi.nama if self.divisi else "Tanpa Divisi"
        return f"{self.username} ({self.get_role_display()} - {divisi_nama})"

# ---------------------------------------------------------
# 4. KONTAK & PELANGGAN
# ---------------------------------------------------------
class Contact(models.Model):
    nomor_wa = models.CharField(max_length=20, primary_key=True)
    nama = models.CharField(max_length=100, db_index=True)
    total_order = models.IntegerField(default=0)
    total_spent = models.IntegerField(default=0)
    # FIX: Ganti CharField ke DateField agar sorting/filtering tanggal bekerja benar
    last_order = models.DateField(null=True, blank=True)
    keterangan = models.TextField(null=True, blank=True, help_text="Catatan/keterangan tentang pelanggan ini")

    class Meta:
        indexes = [
            # Query: sort pelanggan by last_order (terbaru di atas)
            models.Index(fields=['-last_order'], name='idx_contact_last_order'),
            # Query: sort by total_spent (pelanggan terbesar)
            models.Index(fields=['-total_spent'], name='idx_contact_total_spent'),
        ]

    def __str__(self):
        return self.nama

# ---------------------------------------------------------
# 5. MANAJEMEN PESANAN (INDUK / INVOICE)
# ---------------------------------------------------------
def _generate_order_id():
    """Auto-generate ID order: ORD-YYYYMMDD-XXXX"""
    from django.utils import timezone
    today    = timezone.now().strftime('%Y%m%d')
    short_id = uuid.uuid4().hex[:4].upper()
    return f'ORD-{today}-{short_id}'

class Order(models.Model):
    STATUS_GLOBAL_CHOICES = (
        ('review', 'Menunggu Review Manager'),
        ('desain', 'Proses Desain'),
        ('proses', 'Dalam Proses Produksi'),
        ('ready', 'Siap Diambil / Selesai Produksi'),
        ('selesai', 'Selesai Seluruhnya'),
        ('batal', 'Dibatalkan / Cancel'),
    )

    id = models.CharField(max_length=50, primary_key=True, default=_generate_order_id)
    waktu = models.DateTimeField(default=timezone.now, db_index=True)
    
    # Hubungkan ke model Contact (Foreign Key lebih baik, tapi pakai CharField juga tidak masalah jika existingnya begitu)
    nomor_wa = models.CharField(max_length=20, db_index=True)
    nama = models.CharField(max_length=100, db_index=True)
    status_global = models.CharField(max_length=30, choices=STATUS_GLOBAL_CHOICES, default='review', db_index=True)
    catatan_pelanggan = models.TextField(null=True, blank=True)
    metode_pembayaran = models.CharField(max_length=20, default='tunai')

    # TAMBAHAN FIELD MODUL 1: KEUANGAN & DISKON
    dp_dibayar = models.IntegerField(default=0, help_text="Uang muka yang sudah dibayar")
    diskon_persen = models.FloatField(default=0.0, help_text="Diskon nota dalam persen, 0-100")
    total_harga = models.IntegerField(default=0, help_text="Total harga keseluruhan setelah diskon")
    sisa_tagihan = models.IntegerField(default=0, help_text="total_harga dikurangi dp_dibayar")

    def update_totals(self):
        """Method bantuan untuk menghitung ulang total dan sisa tagihan dari item-itemnya."""
        subtotal = sum(item.harga_jual for item in self.items.all())
        potongan = int(subtotal * (self.diskon_persen / 100))
        self.total_harga = subtotal - potongan
        self.sisa_tagihan = max(0, self.total_harga - self.dp_dibayar)
        # Jangan pakai self.save() di sini jika dipanggil dari signal/save, agar tidak infinite loop

    def save(self, *args, **kwargs):
        # Hitung ulang total_harga dari item-itemnya secara dinamis jika order sudah ada
        if self.pk:
            try:
                subtotal = sum(item.harga_jual for item in self.items.all())
                potongan = int(subtotal * (self.diskon_persen / 100))
                self.total_harga = subtotal - potongan
            except Exception:
                pass

        # Auto kalkulasi sisa tagihan setiap kali nota disimpan
        self.sisa_tagihan = max(0, self.total_harga - self.dp_dibayar)
        super().save(*args, **kwargs)

        # Sinkronisasi status job board jika order dibatalkan (batal) atau selesai (selesai)
        if self.status_global == 'batal':
            try:
                # Import lokal untuk mencegah circular import
                from .models import JobBoard
                JobBoard.objects.filter(order_item__order=self).exclude(status_pekerjaan__in=['selesai', 'batal']).update(
                    status_pekerjaan='batal',
                    waktu_selesai=timezone.now()
                )
            except Exception:
                pass
        elif self.status_global == 'selesai':
            try:
                # Import lokal untuk mencegah circular import
                from .models import JobBoard
                JobBoard.objects.filter(order_item__order=self).exclude(status_pekerjaan__in=['selesai', 'batal', 'gagal']).update(
                    status_pekerjaan='selesai',
                    waktu_selesai=timezone.now()
                )
            except Exception:
                pass

    def __str__(self):
        return f"{self.id} - {self.nama}"

# ---------------------------------------------------------
# 6. DETAIL ITEM PESANAN (MENDUKUNG 1 ID NOTA BANYAK ITEM)
# ---------------------------------------------------------
class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    jenis_produk = models.CharField(max_length=100, db_index=True)
    
    # TAMBAHAN FIELD MODUL 1: KALKULATOR PERCETAKAN
    panjang = models.FloatField(default=0.0, help_text="Panjang cetakan dalam meter")
    lebar = models.FloatField(default=0.0, help_text="Lebar cetakan dalam meter")
    luas = models.FloatField(default=0.0, help_text="Otomatis dihitung: panjang x lebar")
    bahan = models.CharField(max_length=100, null=True, blank=True, help_text="Misal: Flexi Korea, Vinyl, dll")
    harga_per_m2 = models.IntegerField(default=0, help_text="Harga satuan bahan per m2")
    
    # Format Tabel Excel disimpan di sini dalam bentuk JSON (termasuk list Finishing)
    detail = models.JSONField(default=list, null=True, blank=True, help_text="Spesifikasi awal dari customer dalam format Tabel/JSON") 
    
    qty = models.IntegerField(default=1)
    harga_jual = models.IntegerField(default=0)
    biaya_bahan = models.IntegerField(default=0)
    estimasi = models.CharField(max_length=50, default="-")
    gdrive_customer_link = models.URLField(max_length=500, null=True, blank=True)
    keterangan_detail = models.TextField(null=True, blank=True, help_text="Keterangan khusus/detail cetak dari CS")

    class Meta:
        indexes = [
            # Query: semua item dari satu order (paling sering)
            models.Index(fields=['order'], name='idx_orderitem_order'),
            # Query: filter jenis produk tertentu lintas order
            models.Index(fields=['jenis_produk'], name='idx_orderitem_jenis'),
        ]

    def save(self, *args, **kwargs):
        # Auto kalkulasi luas m2 dari P x L sebelum disimpan ke database
        self.luas = round(self.panjang * self.lebar, 4)
        super().save(*args, **kwargs)

        # BUG FIX: Gunakan update_fields agar Order.save() tidak dipanggil penuh
        # (mencegah infinite loop karena Order.save() tidak memanggil item.save()).
        # Akses via pk untuk menghindari kalkulasi ulang jika order belum di-load.
        try:
            from django.db.models import Sum
            order = self.order
            subtotal = order.items.aggregate(total=Sum('harga_jual'))['total'] or 0
            potongan = int(subtotal * (order.diskon_persen / 100))
            total_harga = subtotal - potongan
            sisa_tagihan = max(0, total_harga - order.dp_dibayar)
            # Pakai queryset update agar tidak trigger Order.save() sama sekali
            Order.objects.filter(pk=order.pk).update(
                total_harga=total_harga,
                sisa_tagihan=sisa_tagihan,
            )
        except Exception:
            pass  # Jangan crash OrderItem.save() hanya karena update total gagal

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
    status_pekerjaan = models.CharField(max_length=20, choices=STATUS_JOB_CHOICES, default='antrean', db_index=True) 
    
    # Catatan hasil modifikasi/interview staff berbentuk tabel Excel (JSON)
    catatan_staff = models.JSONField(default=list, null=True, blank=True, help_text="Keterangan staff berformat Tabel/Excel (JSON)")
    gdrive_output_link = models.URLField(max_length=500, null=True, blank=True, help_text="Link file hasil kerja staff di Google Drive")
    alasan_gagal = models.TextField(null=True, blank=True, help_text="Alasan kenapa pengerjaan job ini gagal atau dibatalkan")
    
    # Nominal insentif ditentukan secara manual oleh Manager
    insentif = models.IntegerField(default=0, help_text="Insentif yang ditentukan oleh Manager untuk tugas ini")
    biaya_desain = models.IntegerField(default=0, help_text="Biaya tambahan desain untuk tugas ini")
    
    otp_code = models.CharField(max_length=10, blank=True, default="")
    otp_requested = models.BooleanField(default=False)
    otp_sent = models.BooleanField(default=False)
    
    waktu_mulai = models.DateTimeField(null=True, blank=True)
    waktu_selesai = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            # Query terbanyak: job milik seorang staff dengan status tertentu
            models.Index(fields=['pic_staff', 'status_pekerjaan'], name='idx_job_staff_status'),
            # Query: job per staff + bulan selesai (untuk timecard & insentif)
            models.Index(fields=['pic_staff', 'waktu_selesai'], name='idx_job_staff_selesai'),
            # Query: job per tahap produksi (papan kanban view)
            models.Index(fields=['tahap', 'status_pekerjaan'], name='idx_job_tahap_status'),
            # Query: semua job dari satu order item
            models.Index(fields=['order_item'], name='idx_job_orderitem'),
            # Query: filter job yang ada OTP request pending (admin view)
            models.Index(fields=['otp_requested', 'otp_sent'], name='idx_job_otp_flags'),
        ]

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
    nama          = models.CharField(max_length=100, db_index=True)
    stok          = models.FloatField(default=0.0)
    satuan        = models.CharField(max_length=20)
    kategori      = models.CharField(max_length=50, db_index=True)
    min_stok      = models.FloatField(default=0.0)
    cost_per_unit = models.FloatField(default=0.0)
    supplier      = models.CharField(max_length=100, default='Unknown', db_index=True)

    class Meta:
        indexes = [
            # Query: filter item per kategori + sort nama
            models.Index(fields=['kategori', 'nama'], name='idx_inv_kategori_nama'),
            # Query: item stok di bawah min_stok (notifikasi stok rendah)
            models.Index(fields=['stok', 'min_stok'], name='idx_inv_stok_min'),
        ]

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
    waktu      = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-waktu']
        indexes = [
            # Query: riwayat per item diurutkan waktu (paling sering)
            models.Index(fields=['item', '-waktu'], name='idx_restock_item_waktu'),
            # Query: riwayat per user (siapa yang stok/ambil)
            models.Index(fields=['user', '-waktu'], name='idx_restock_user_waktu'),
        ]

    def __str__(self):
        return f"{self.item.nama} {'+' if self.delta >= 0 else ''}{self.delta} ({self.waktu:%Y-%m-%d})"


class ProductPrice(models.Model):
    kategori = models.CharField(max_length=50, db_index=True)
    nama_produk = models.CharField(max_length=100, db_index=True)
    harga = models.IntegerField(default=0)
    material = models.CharField(max_length=100, null=True, blank=True)
    price_type = models.CharField(max_length=20, default='flat', db_index=True)
    tiers = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            # Query: cari produk by kategori (dropdown filter pricelist)
            models.Index(fields=['kategori', 'nama_produk'], name='idx_price_kat_produk'),
            # Query: cari by kategori + material (kalkulasi harga)
            models.Index(fields=['kategori', 'material'], name='idx_price_kat_material'),
        ]

    def __str__(self):
        return f"{self.nama_produk} - Rp{self.harga}"

# FIX: Rename dari AppConfig -> SystemConfig untuk hindari konflik dengan django.apps.AppConfig
class SystemConfig(models.Model):
    key = models.CharField(max_length=50, primary_key=True)
    value = models.TextField()

    def __str__(self):
        return self.key

class FAQ(models.Model):
    pertanyaan = models.CharField(max_length=200, primary_key=True)
    jawaban = models.TextField()

    def __str__(self):
        return self.pertanyaan