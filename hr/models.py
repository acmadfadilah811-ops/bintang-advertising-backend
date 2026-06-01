from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# 1. ABSENSI — Clock-in / Clock-out harian
# ---------------------------------------------------------------------------

class Absensi(models.Model):
    STATUS_CHOICES = [
        ("hadir", "Hadir"),
        ("izin", "Izin"),
        ("sakit", "Sakit"),
        ("alpha", "Alpha"),
        ("wfh", "Work From Home"),
    ]

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="absensi",
        limit_choices_to={"role__in": ["staff", "manager"]},
    )
    tanggal = models.DateField(db_index=True)
    jam_masuk = models.DateTimeField(
        null=True, blank=True, help_text="Clock-in — diisi saat staff klik tombol masuk."
    )
    jam_keluar = models.DateTimeField(
        null=True, blank=True, help_text="Clock-out — diisi saat staff klik tombol keluar."
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="hadir")
    workspace_unlocked = models.BooleanField(
        default=False,
        help_text="Bypass checkout/lock status, owner mengizinkan staff mengakses papan kerja meskipun sudah checkout."
    )
    catatan = models.TextField(blank=True, default="")
    diverifikasi = models.BooleanField(
        default=False, help_text="Manager menandai kehadiran sudah diverifikasi."
    )
    diverifikasi_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verifikasi_absensi",
    )

    class Meta:
        db_table = "absensi"
        unique_together = [["staff", "tanggal"]]
        ordering = ["-tanggal"]
        verbose_name = "Absensi"
        verbose_name_plural = "Absensi"

    def __str__(self):
        return f"{self.staff.username} — {self.tanggal} ({self.get_status_display()})"

    @property
    def durasi_kerja_jam(self):
        """Hitung durasi kerja dalam jam (float). Contoh: 8.5 = 8 jam 30 menit."""
        if self.jam_masuk and self.jam_keluar:
            delta = self.jam_keluar - self.jam_masuk
            return round(delta.total_seconds() / 3600, 2)
        return 0.0

    @property
    def sudah_clock_out(self):
        return self.jam_keluar is not None


# ---------------------------------------------------------------------------
# 1B. SESI ABSENSI HARIAN & IZIN KETERLAMBATAN
# ---------------------------------------------------------------------------

class DailyAttendanceSession(models.Model):
    tanggal = models.DateField(unique=True, db_index=True)
    waktu_mulai = models.DateTimeField(help_text="Kapan absensi mulai dibuka.")
    batas_maksimal = models.DateTimeField(help_text="Batas akhir waktu absen sebelum akun terkunci.")
    is_active = models.BooleanField(default=True, help_text="Apakah sesi hari ini sedang berlangsung.")
    dihidupkan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sesi_absensi_dihidupkan",
    )
    dibuat_pada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "daily_attendance_session"
        ordering = ["-tanggal"]
        verbose_name = "Sesi Absensi Harian"
        verbose_name_plural = "Sesi Absensi Harian"

    def __str__(self):
        status = "Aktif" if self.is_active else "Selesai"
        return f"Sesi {self.tanggal} ({status})"


class UnlockRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Menunggu Persetujuan"),
        ("approved", "Disetujui"),
        ("rejected", "Ditolak"),
    ]

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="unlock_requests",
    )
    sesi = models.ForeignKey(
        DailyAttendanceSession,
        on_delete=models.CASCADE,
        related_name="unlock_requests",
    )
    alasan = models.TextField(help_text="Alasan terlambat atau tidak absen.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    waktu_request = models.DateTimeField(auto_now_add=True)
    direspon_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="unlock_requests_direspon",
    )
    waktu_respon = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "unlock_request"
        ordering = ["-waktu_request"]
        verbose_name = "Permintaan Buka Akses"
        verbose_name_plural = "Permintaan Buka Akses"

    def __str__(self):
        return f"[{self.get_status_display()}] {self.staff.username} - {self.sesi.tanggal}"


# ---------------------------------------------------------------------------
# 2. KONTRAK — Data kontrak kerja per staff
# ---------------------------------------------------------------------------

class Kontrak(models.Model):
    TIPE_CHOICES = [
        ("tetap", "Karyawan Tetap"),
        ("kontrak", "Kontrak"),
        ("magang", "Magang"),
        ("freelance", "Freelance"),
        ("harian", "Harian Lepas"),
    ]
    STATUS_CHOICES = [
        ("aktif", "Aktif"),
        ("berakhir", "Berakhir"),
        ("diputus", "Diputus"),
    ]

    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="kontrak",
    )
    nomor_kontrak = models.CharField(max_length=50, unique=True)
    tipe = models.CharField(max_length=20, choices=TIPE_CHOICES)
    tanggal_mulai = models.DateField()
    tanggal_berakhir = models.DateField(
        null=True, blank=True, help_text="Kosong = tidak ada batas waktu (karyawan tetap)."
    )
    gaji_pokok = models.IntegerField(default=0, help_text="Dalam Rupiah.")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="aktif")
    dokumen = models.FileField(
        upload_to="kontrak/%Y/", null=True, blank=True, help_text="Upload PDF kontrak."
    )
    catatan = models.TextField(blank=True, default="")
    dibuat_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="kontrak_dibuat",
    )
    dibuat_pada = models.DateTimeField(auto_now_add=True)
    diperbarui_pada = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "kontrak"
        ordering = ["-dibuat_pada"]
        verbose_name = "Kontrak"
        verbose_name_plural = "Kontrak"

    def __str__(self):
        return f"{self.nomor_kontrak} — {self.staff.username} ({self.get_tipe_display()})"

    @property
    def is_aktif(self):
        return self.status == "aktif"


# ---------------------------------------------------------------------------
# 3. PENGUMUMAN / INFO STAFF
# ---------------------------------------------------------------------------

class StaffAnnouncement(models.Model):
    TARGET_CHOICES = [
        ("semua", "Semua Staff"),
        ("divisi", "Per Divisi"),
        ("personal", "Personal"),
    ]

    judul = models.CharField(max_length=200)
    isi = models.TextField()
    target = models.CharField(max_length=10, choices=TARGET_CHOICES, default="semua")
    divisi = models.ForeignKey(
        "api.Divisi",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Wajib diisi jika target = 'divisi'.",
    )
    staff_personal = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pengumuman_personal",
        help_text="Wajib diisi jika target = 'personal'.",
    )
    dibuat_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pengumuman_dibuat",
    )
    aktif_sampai = models.DateField(
        null=True, blank=True, help_text="Kosong = tidak ada batas tampil."
    )
    dibuat_pada = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_announcement"
        ordering = ["-dibuat_pada"]
        verbose_name = "Pengumuman"
        verbose_name_plural = "Pengumuman"

    def __str__(self):
        return f"[{self.get_target_display()}] {self.judul}"

# ---------------------------------------------------------
# 9. FINANCE & BUKU BESAR (GENERAL LEDGER)
# ---------------------------------------------------------
class Akun(models.Model):
    kode_akun = models.CharField(max_length=20, unique=True, help_text="Misal: 1-100, 4-200")
    nama_akun = models.CharField(max_length=100, help_text="Misal: Kas Besar, Pendapatan Jasa")
    kategori = models.CharField(max_length=50, help_text="Misal: Aset, Kewajiban, Ekuitas, Pendapatan, Beban")
    
    def __str__(self):
        return f"{self.kode_akun} - {self.nama_akun}"

class TransaksiBukuBesar(models.Model):
    akun = models.ForeignKey(Akun, on_delete=models.CASCADE, related_name='transaksi')
    tanggal = models.DateField()
    no_referensi = models.CharField(max_length=50, blank=True, null=True, help_text="Bisa diisi ID Order atau Nomor Kwitansi")
    keterangan = models.TextField()
    
    # Menyimpan nominal transaksi
    debit = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    kredit = models.DecimalField(max_digits=15, decimal_places=0, default=0)
    
    waktu_input = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tanggal', 'waktu_input']
        indexes = [
            # Query: filter transaksi berdasarkan rentang tanggal (sangat penting untuk laporan bulanan/tahunan)
            models.Index(fields=['tanggal', 'waktu_input'], name='idx_tx_tanggal_waktu'),
            # Query: filter transaksi per akun tertentu berdasarkan tanggal
            models.Index(fields=['akun', 'tanggal'], name='idx_tx_akun_tanggal'),
            # Query: pencarian berdasarkan nomor referensi (misal ID Order)
            models.Index(fields=['no_referensi'], name='idx_tx_no_referensi'),
        ]

    def __str__(self):
        return f"{self.tanggal} | {self.akun.nama_akun} | D: {self.debit} | K: {self.kredit}"
