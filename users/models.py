from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    """
    Profil ekstensi untuk CustomUser.
    Menyimpan data permission, akses modul, dan tracking aktivitas.
    Mengadopsi pola Django CRM: User → Profile.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # --- Akses & Permission ---
    is_organization_admin = models.BooleanField(
        default=False,
        help_text="Jika True, user memiliki akses penuh setara Owner.",
    )
    has_production_access = models.BooleanField(
        default=True,
        help_text="Akses ke modul Produksi (Order, Job Board).",
    )
    has_finance_access = models.BooleanField(
        default=False,
        help_text="Akses ke laporan keuangan dan data omset.",
    )

    # --- Tracking Aktivitas Real-time ---
    last_seen = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp terakhir user aktif (diupdate middleware).",
    )
    date_of_joining = models.DateField(
        null=True,
        blank=True,
        help_text="Tanggal mulai bergabung.",
    )

    class Meta:
        db_table = "user_profile"
        verbose_name = "Profil User"
        verbose_name_plural = "Profil User"

    def __str__(self):
        return f"Profile({self.user.username} | {self.user.role})"

    # --- Computed Properties ---

    @property
    def is_online(self):
        """True jika last_seen kurang dari 15 menit yang lalu."""
        if not self.last_seen:
            return False
        return (timezone.now() - self.last_seen) < timedelta(minutes=15)

    @property
    def role(self):
        """Shortcut ke role dari CustomUser."""
        return getattr(self.user, "role", "staff")

    @property
    def divisi(self):
        """Shortcut ke divisi dari CustomUser."""
        return getattr(self.user, "divisi", None)


# ---------------------------------------------------------------------------


class SessionToken(models.Model):
    """
    Melacak sesi JWT aktif per user.
    Diadopsi dari Django CRM — memungkinkan revoke token spesifik
    dan audit siapa login dari device/IP mana.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="session_tokens",
    )
    token_jti = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="JWT ID (jti claim) — digunakan untuk identifikasi & revoke.",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    device_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Label device, misal: Chrome di Windows 10.",
    )
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "session_token"
        ordering = ["-created_at"]
        verbose_name = "Session Token"
        verbose_name_plural = "Session Tokens"

    def __str__(self):
        status = "Aktif" if self.is_active else "Dicabut"
        return f"{self.user.username} | {self.token_jti[:10]}... | {status}"

    def revoke(self):
        """Cabut (revoke) token ini — user harus login ulang."""
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_active", "revoked_at"])

    @classmethod
    def cleanup_expired(cls):
        """Hapus token kedaluwarsa. Panggil via cron/scheduled task."""
        return cls.objects.filter(expires_at__lt=timezone.now()).delete()


# ---------------------------------------------------------------------------


class SecurityAuditLog(models.Model):
    """
    Rekam semua event keamanan penting.
    Diadopsi dari Django CRM security_audit.log — tapi disimpan di DB
    agar bisa ditampilkan di dashboard Owner.
    """

    EVENT_CHOICES = [
        ("LOGIN_SUCCESS", "Login Berhasil"),
        ("LOGIN_FAILED", "Login Gagal"),
        ("LOGOUT", "Logout"),
        ("TOKEN_REFRESH", "Refresh Token"),
        ("TOKEN_REVOKED", "Token Dicabut"),
        ("PASSWORD_CHANGED", "Ganti Password"),
        ("PASSWORD_RESET_REQUESTED", "Reset Password Diminta"),
        ("PASSWORD_RESET_FAILED", "Reset Password Gagal"),
        ("PASSWORD_RESET_SUCCEEDED", "Reset Password Berhasil"),
        ("PERMISSION_DENIED", "Akses Ditolak"),
        ("USER_CREATED", "User Baru Dibuat"),
        ("USER_DEACTIVATED", "User Dinonaktifkan"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        help_text="Null saat login gagal (user tidak diketahui).",
    )
    # Simpan username mentah untuk kasus login gagal (user tidak ada di DB)
    username_input = models.CharField(max_length=150, blank=True, default="")
    event = models.CharField(max_length=30, choices=EVENT_CHOICES, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    keterangan = models.TextField(blank=True, default="")
    berhasil = models.BooleanField(default=True, db_index=True)
    waktu = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "security_audit_log"
        ordering = ["-waktu"]
        verbose_name = "Security Audit Log"
        verbose_name_plural = "Security Audit Logs"

    def __str__(self):
        icon = "✓" if self.berhasil else "✗"
        nama = self.user.username if self.user else (self.username_input or "Unknown")
        return f"{icon} [{self.get_event_display()}] {nama} — {self.waktu:%Y-%m-%d %H:%M}"
