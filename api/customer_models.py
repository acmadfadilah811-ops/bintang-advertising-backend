from django.conf import settings
from django.db import models


class CustomerGroup(models.Model):
    """Tipe Pelanggan (Pelanggan & Supplier > Tipe Pelanggan) — pengelompokan pelanggan
    (mis. Reseller, VIP) beserta diskon khusus per grup."""
    nama = models.CharField(max_length=100, unique=True)
    diskon_persen = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='customer_groups')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['nama']

    def __str__(self):
        return self.nama


class Customer(models.Model):
    """Pelanggan (Pelanggan & Supplier > Pelanggan) — entitas pelanggan mandiri, terpisah dari
    Contact (kontak WhatsApp/Order) sesuai keputusan agar tidak bercampur dengan alur order WA."""
    GENDER_CHOICES = [('L', 'Laki-laki'), ('P', 'Perempuan')]

    nama = models.CharField(max_length=255)
    kode_pelanggan = models.CharField(max_length=50, blank=True, default='')
    customer_group = models.ForeignKey(CustomerGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='customers')
    handphone = models.CharField(max_length=20, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    password = models.CharField(max_length=128, blank=True, default='', help_text="Opsional — login Toko Online/Pesan Online. Disimpan ter-hash.")
    jenis_kelamin = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, default='')
    tanggal_lahir = models.DateField(null=True, blank=True)
    nama_perusahaan = models.CharField(max_length=255, blank=True, default='')
    batas_kredit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    loyalty_points = models.PositiveIntegerField(default=0)
    terima_buletin = models.BooleanField(default=False)
    bekukan = models.BooleanField(default=False, help_text="Bekukan: pelanggan tidak bisa dipilih di POS")
    tanggal_berakhir = models.DateField(null=True, blank=True, help_text="Masa berlaku membership, kosongkan jika tidak ada")
    catatan = models.TextField(blank=True, default='')
    alamat = models.TextField(blank=True, default='')
    negara = models.CharField(max_length=100, blank=True, default='')
    provinsi = models.CharField(max_length=100, blank=True, default='')
    kota = models.CharField(max_length=100, blank=True, default='')
    kecamatan = models.CharField(max_length=100, blank=True, default='')
    kode_pos = models.CharField(max_length=20, blank=True, default='')
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='customers')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nama


class CustomerNoteTag(models.Model):
    """Tag bebas untuk Catatan Pelanggan — dibuat otomatis (get-or-create) saat diketik pertama kali."""
    nama = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ['nama']

    def __str__(self):
        return self.nama


class CustomerNote(models.Model):
    """Catatan Pelanggan — satu 'kasus'/topik terkait pelanggan (judul, tanggal, tag, lampiran),
    yang bisa memuat banyak entri catatan (CustomerNoteEntry) sebagai log/riwayat."""

    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='notes')
    customer_name = models.CharField(max_length=255, blank=True, default='', help_text="Snapshot nama saat dibuat, tetap terisi walau customer dihapus")
    judul = models.CharField(max_length=255, blank=True, default='')
    tanggal = models.DateField(null=True, blank=True)
    jam = models.TimeField(null=True, blank=True)
    tags = models.ManyToManyField(CustomerNoteTag, blank=True, related_name='notes')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='customer_notes')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Catatan {self.customer_name or 'Umum'} — {self.judul or '(tanpa judul)'}"


class CustomerNoteEntry(models.Model):
    """Entri catatan berulang (label opsional + isi) di dalam satu CustomerNote, seperti log/riwayat."""
    note = models.ForeignKey(CustomerNote, on_delete=models.CASCADE, related_name='entries')
    label = models.CharField(max_length=100, blank=True, default='')
    content = models.TextField()
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='customer_note_entries')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.label or 'Catatan'} (note #{self.note_id})"


def customer_note_document_path(instance, filename):
    return f'customer_notes/{instance.note_id}/{filename}'


class CustomerNoteDocument(models.Model):
    """Lampiran dokumen (gambar/PDF, maks. 5 per catatan, maks. 5MB/file)."""
    note = models.ForeignKey(CustomerNote, on_delete=models.CASCADE, related_name='documents')
    file = models.FileField(upload_to=customer_note_document_path)
    original_name = models.CharField(max_length=255, blank=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name or self.file.name


class CustomerReview(models.Model):
    """Ulasan Pelanggan — rating & komentar terkait kualitas produk/layanan."""
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews')
    customer_name = models.CharField(max_length=255, blank=True, default='')
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='customer_reviews')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Ulasan {self.customer_name or 'Umum'} ({self.rating} bintang)"


class Supplier(models.Model):
    """Supplier (Pelanggan & Supplier > Supplier) — data pemasok bahan baku/produk."""
    nama = models.CharField(max_length=255)
    kontak_pic = models.CharField(max_length=255, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    catatan = models.TextField(blank=True, default='')
    negara = models.CharField(max_length=100, blank=True, default='')
    provinsi = models.CharField(max_length=100, blank=True, default='')
    kota = models.CharField(max_length=100, blank=True, default='')
    kode_pos = models.CharField(max_length=20, blank=True, default='')
    alamat = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='suppliers')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nama
