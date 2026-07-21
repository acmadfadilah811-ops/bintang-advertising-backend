from django.conf import settings
from django.db import models

TIPE_DISKON_CHOICES = [('percent', 'Persen'), ('nominal', 'Nominal')]
KANAL_POS = 'pos'
KANAL_ONLINE = 'online'


class SalesDiscount(models.Model):
    """Diskon Penjualan (Marketing > Voucher & Diskon > Diskon Penjualan) — hanya berlaku
    untuk Toko Online sesuai panduan Olsera, diterapkan otomatis saat total pesanan terpenuhi."""
    tanggal_aktif = models.DateField()
    tanpa_kadaluarsa = models.BooleanField(default=True)
    tanggal_kadaluarsa = models.DateField(null=True, blank=True)
    minimal_total_pesanan = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tipe_diskon = models.CharField(max_length=10, choices=TIPE_DISKON_CHOICES, default='percent')
    jumlah_diskon = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tipe_pelanggan = models.CharField(max_length=255, blank=True, default='')
    brand_legacy = models.CharField(max_length=255, blank=True, default='')
    brand = models.ManyToManyField('Brand', blank=True, related_name='sales_discounts')
    catatan = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales_discounts')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        unit = '%' if self.tipe_diskon == 'percent' else ''
        return f"Diskon {self.jumlah_diskon}{unit} ({self.tanggal_aktif})"


class DiscountCoupon(models.Model):
    """Kupon Diskon (Marketing > Voucher & Diskon > Kupon Diskon) — kode voucher yang bisa
    dipakai di POS dan/atau Online Order sesuai kriteria & batas penggunaan yang diatur."""
    kode = models.CharField(max_length=12, unique=True)
    judul = models.CharField(max_length=255)
    tanggal_aktif = models.DateField(null=True, blank=True)
    tanpa_kadaluarsa = models.BooleanField(default=True)
    tanggal_kadaluarsa = models.DateField(null=True, blank=True)
    min_total_pesanan = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    unlimited_usage = models.BooleanField(default=True)
    batas_penggunaan = models.IntegerField(null=True, blank=True)
    show_pos = models.BooleanField(default=True)
    show_online = models.BooleanField(default=False)
    once_per_customer = models.BooleanField(default=False)
    daily_reuse = models.BooleanField(default=False)
    dine_in = models.BooleanField(default=False)
    delivery = models.BooleanField(default=False)
    take_away = models.BooleanField(default=False)
    reservasi = models.BooleanField(default=False)
    tipe_diskon = models.CharField(max_length=10, choices=TIPE_DISKON_CHOICES, default='percent')
    jumlah_diskon = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    maksimal_jumlah_diskon = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    all_customers = models.BooleanField(default=True)
    tipe_pelanggan = models.CharField(max_length=255, blank=True, default='')
    pelanggan_legacy = models.CharField(max_length=255, blank=True, default='')
    pelanggan = models.ManyToManyField('Contact', blank=True, related_name='discount_coupons')
    all_products = models.BooleanField(default=True)
    grup_produk_legacy = models.CharField(max_length=255, blank=True, default='')
    grup_produk = models.ManyToManyField('ProductCategory', blank=True, related_name='discount_coupons')
    produk_legacy = models.CharField(max_length=255, blank=True, default='')
    produk = models.ManyToManyField('Product', blank=True, related_name='discount_coupons')
    all_packages = models.BooleanField(default=True)
    paket_produk_legacy = models.CharField(max_length=255, blank=True, default='')
    paket_produk = models.ManyToManyField('ProductPackage', blank=True, related_name='discount_coupons')
    all_brands = models.BooleanField(default=True)
    brand_legacy = models.CharField(max_length=255, blank=True, default='')
    brand = models.ManyToManyField('Brand', blank=True, related_name='discount_coupons')
    penggunaan_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='discount_coupons')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.kode} - {self.judul}"


class POSPromotion(models.Model):
    """Promosi (POS) (Marketing > Voucher & Diskon > Promosi (POS)) — 4 tipe sesuai Olsera:
    BX (beli produk tertentu gratis produk lain), DQ (diskon kuantitas), DA (diskon total
    transaksi), FI (gratis produk)."""
    TIPE_PROMOSI_CHOICES = [
        ('BX', 'Beli Produk Tertentu, Gratis Produk Lain'),
        ('DQ', 'Diskon Jika Membeli Produk dengan Kuantitas Tertentu'),
        ('DA', 'Diskon Jika Memenuhi Total Transaksi yang Ditentukan'),
        ('FI', 'Gratis Produk'),
    ]
    BERLAKU_MEMBELI_CHOICES = [
        ('salah-satu', 'Salah satu produk yang diatur'),
        ('semua', 'Semua produk yang diatur'),
    ]

    judul = models.CharField(max_length=255)
    tipe_promosi = models.CharField(max_length=5, choices=TIPE_PROMOSI_CHOICES, default='BX')
    combine_qty = models.BooleanField(default=True)
    combine_qty_value = models.IntegerField(default=1)
    produk_qty = models.JSONField(default=list, blank=True)
    grup_produk_legacy = models.CharField(max_length=255, blank=True, default='')
    grup_produk = models.ManyToManyField('ProductCategory', blank=True, related_name='pos_promotions')
    paket_produk_legacy = models.CharField(max_length=255, blank=True, default='')
    paket_produk = models.ManyToManyField('ProductPackage', blank=True, related_name='pos_promotions')
    brand_legacy = models.CharField(max_length=255, blank=True, default='')
    brand = models.ManyToManyField('Brand', blank=True, related_name='pos_promotions')
    berlaku_membeli = models.CharField(max_length=20, choices=BERLAKU_MEMBELI_CHOICES, default='semua')
    produk_gratis_legacy = models.CharField(max_length=255, blank=True, default='')
    produk_gratis = models.ManyToManyField('Product', blank=True, related_name='pos_promotions_gratis')
    qty_gratis = models.IntegerField(default=1)
    min_total_transaksi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tipe_diskon = models.CharField(max_length=10, choices=TIPE_DISKON_CHOICES, default='percent')
    jumlah_diskon = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    berlaku_kelipatan = models.BooleanField(default=False)
    all_customers = models.BooleanField(default=True)
    tipe_pelanggan = models.CharField(max_length=255, blank=True, default='')
    pelanggan_legacy = models.CharField(max_length=255, blank=True, default='')
    pelanggan = models.ManyToManyField('Contact', blank=True, related_name='pos_promotions')
    tanggal_aktif = models.DateField()
    tanpa_kadaluarsa = models.BooleanField(default=True)
    tanggal_kadaluarsa = models.DateField(null=True, blank=True)
    jam_24 = models.BooleanField(default=True)
    jam_mulai = models.TimeField(null=True, blank=True)
    jam_berakhir = models.TimeField(null=True, blank=True)
    hari = models.CharField(max_length=100, default='min,sen,sel,rab,kam,jum,sab')
    is_active = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_promotions')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.judul} ({self.tipe_promosi})"


class CouponUsage(models.Model):
    """Riwayat penggunaan kupon diskon oleh pelanggan dalam transaksi POS/Order."""
    kupon = models.ForeignKey(DiscountCoupon, on_delete=models.CASCADE, related_name='usages')
    pelanggan = models.ForeignKey('Contact', on_delete=models.SET_NULL, null=True, blank=True, related_name='coupon_usages')
    pos_sale = models.ForeignKey('POSSale', on_delete=models.CASCADE, null=True, blank=True, related_name='coupon_usages')
    order = models.ForeignKey('Order', on_delete=models.CASCADE, null=True, blank=True, related_name='coupon_usages')
    kanal = models.CharField(max_length=10, default=KANAL_POS)
    nilai_diskon = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tanggal = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Penggunaan {self.kupon.kode} - {self.nilai_diskon}"
