from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# Pendapatan/Pengeluaran (Kas Masuk/Keluar) — mencatat pemasukan/pengeluaran
# di LUAR penjualan harian (mis. listrik, air galon, tips). Setara fitur
# "Pendapatan/Pengeluaran" Olsera: dua tab (Transaksi + Tipe Transaksi).
# ---------------------------------------------------------------------------
DIRECTION_CHOICES = [
    ('pendapatan', 'Pendapatan'),
    ('pengeluaran', 'Pengeluaran'),
]


class CashTransactionType(models.Model):
    """Tipe Transaksi (master) — jenis pemasukan/pengeluaran, mis. 'Listrik'."""
    nama = models.CharField(max_length=255)
    tipe = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cash_transaction_types',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nama']

    def __str__(self):
        return f"{self.nama} ({self.tipe})"


class CashTransaction(models.Model):
    """Entri Pendapatan/Pengeluaran. Arah (pendapatan/pengeluaran) mengikuti tipe."""
    nomor = models.CharField(max_length=50, unique=True, blank=True)
    arah = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    jumlah = models.DecimalField(max_digits=15, decimal_places=2)
    tipe_transaksi = models.ForeignKey(
        CashTransactionType, on_delete=models.PROTECT, related_name='transactions',
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cash_transactions_as_staff', help_text="Staff yang melakukan transaksi",
    )
    waktu = models.DateTimeField(help_text="Tanggal & jam transaksi")
    catatan = models.TextField(blank=True, default='')
    dibuat_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cash_transactions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-waktu', '-created_at']

    def __str__(self):
        return f"{self.nomor} - {self.arah} {self.jumlah}"


class CashTransactionAttachment(models.Model):
    """Lampiran bukti (foto/dokumen) untuk sebuah transaksi (opsional, bisa banyak)."""
    transaction = models.ForeignKey(CashTransaction, on_delete=models.CASCADE, related_name='lampiran')
    file = models.FileField(upload_to='cash_transactions/')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Lampiran {self.transaction.nomor}"
