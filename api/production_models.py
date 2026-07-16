"""Model biaya produksi.

Dipisah dari product_models.py yang sudah 408 baris (di atas batas 400 pada
AGENTS.md). Ditemukan oleh api/models.py lewat `from .production_models import *`,
pola yang sama dengan product_models / customer_models / marketing_models.
"""

from django.db import models


class ProductionCost(models.Model):
    """Master komponen biaya produksi non-bahan: tenaga kerja, listrik, sewa mesin.

    `nilai` di sini adalah nilai DEFAULT. Tiap dokumen produksi menyalinnya lalu
    boleh menimpanya, karena biaya sebenarnya (mis. listrik) berbeda tiap
    produksi — lihat StockProductionDocumentCost.nilai.
    """

    nama = models.CharField(max_length=255)
    nilai = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Nilai default. Bisa ditimpa per dokumen produksi.",
    )
    # PROTECT: akun yang sudah dipakai biaya produksi tidak boleh terhapus
    # begitu saja — itu akan memutus jejak ke buku besar.
    akun = models.ForeignKey(
        'hr.Akun', on_delete=models.PROTECT, related_name='production_costs',
        help_text="Akun buku besar terkait, mis. Beban Listrik.",
    )

    class Meta:
        ordering = ['nama']

    def __str__(self):
        return f"{self.nama} ({self.nilai})"


class StockProductionDocumentCost(models.Model):
    """Biaya produksi yang dibebankan ke satu dokumen produksi."""

    document = models.ForeignKey(
        'api.StockProductionDocument', on_delete=models.CASCADE, related_name='biaya',
    )
    # PROTECT: master biaya yang masih dipakai dokumen tidak boleh dihapus,
    # supaya nilai historis dokumen lama tidak kehilangan acuannya.
    production_cost = models.ForeignKey(
        ProductionCost, on_delete=models.PROTECT, related_name='document_costs',
    )
    nilai = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Nilai untuk dokumen ini. Awalnya disalin dari master, boleh diubah.",
    )

    class Meta:
        ordering = ['id']
        # Satu jenis biaya cukup sekali per dokumen — kalau nilainya berbeda,
        # ubah barisnya, jangan tambah baris kedua. Mencegah total menggelembung
        # karena salah klik.
        unique_together = [('document', 'production_cost')]

    def __str__(self):
        return f"{self.document.nomor} - {self.production_cost.nama}: {self.nilai}"
