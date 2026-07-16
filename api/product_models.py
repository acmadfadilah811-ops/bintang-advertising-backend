from django.conf import settings
from django.db import models

class ProductCategory(models.Model):
    nama = models.CharField(max_length=255)
    key = models.SlugField(max_length=255, unique=True, blank=True, null=True)
    klasifikasi = models.CharField(max_length=255, blank=True, null=True)
    foto = models.ImageField(upload_to='category_photos/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    tampil_pos = models.BooleanField(default=True)
    tampil_nav_web = models.BooleanField(default=True)
    default_price_type = models.CharField(max_length=50, choices=[('flat', 'Flat'), ('tier', 'Tier'), ('per_m2', 'Per M2')], default='flat')
    urutan = models.IntegerField(default=0)

    def __str__(self):
        return self.nama

class Brand(models.Model):
    nama = models.CharField(max_length=255)
    komisi_persen = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.nama

class SpecialType(models.Model):
    nama = models.CharField(max_length=100)
    key = models.SlugField(max_length=100, unique=True)
    icon = models.CharField(max_length=50, blank=True, null=True)
    urutan = models.IntegerField(default=0)

    def __str__(self):
        return self.nama

class Collection(models.Model):
    nama = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.nama

class Product(models.Model):
    PRICE_TYPE_CHOICES = [
        ('flat', 'Flat'),
        ('tier', 'Tier'),
        ('per_m2', 'Per M2'),
    ]

    KONDISI_CHOICES = [
        ('Baru', 'Baru'),
        ('Bekas', 'Bekas'),
    ]

    nama = models.CharField(max_length=255)
    nama_alternatif = models.CharField(max_length=255, blank=True, null=True)
    klasifikasi = models.CharField(max_length=100, blank=True, default='Others')
    kondisi = models.CharField(max_length=10, choices=KONDISI_CHOICES, default='Baru')
    bebas_pajak = models.BooleanField(default=False)
    bebas_biaya_layanan = models.BooleanField(default=False)
    kategori = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, related_name='products')
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    koleksi = models.ForeignKey(Collection, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    tipe_special = models.ForeignKey(SpecialType, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')

    sku = models.CharField(max_length=100, blank=True, null=True, unique=True)
    barcode = models.CharField(max_length=100, blank=True, null=True, unique=True)
    satuan = models.CharField(max_length=50, default='pcs')

    price_type = models.CharField(max_length=50, choices=PRICE_TYPE_CHOICES, default='flat')
    tiers = models.JSONField(blank=True, null=True, help_text="Format: [{'min_qty': 1, 'price': 10000}, ...]")

    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_pasar = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_jual_toko = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_jual_online = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_online_sama = models.BooleanField(default=True)
    harga_dinamis = models.BooleanField(default=False, help_text="Harga jual di toko bersifat dinamis (mengikuti varian)")
    komisi = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    komisi_is_persen = models.BooleanField(default=False, help_text="True = komisi dihitung sebagai %, False = nominal IDR")
    minimal_pesanan = models.PositiveIntegerField(default=1)
    maksimal_pesanan = models.PositiveIntegerField(default=0, help_text="0 = tidak dibatasi")

    lacak_inventori = models.BooleanField(default=True)
    rack = models.CharField(max_length=100, blank=True, default='', help_text="Lokasi rak penyimpanan")
    qty_stok = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    on_hold_qty = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Qty stok yang sedang ditahan/dipesan")
    stok_minimum = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Ambang batas Peringatan Sisa Stok")
    qty_fast_moving = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Ambang batas kualifikasi produk 'fast moving'")

    has_variant = models.BooleanField(default=False)
    tersedia_online = models.BooleanField(default=True)
    tanggal_tersedia_online = models.DateField(null=True, blank=True)
    tidak_tersedia_offline_pos = models.BooleanField(default=False, help_text="Sembunyikan dari POS")
    butuh_pengiriman = models.BooleanField(default=True)
    pesanan_no_seri = models.BooleanField(default=False, help_text="Pesanan disertai pengisian No Seri/IMEI")

    kategori_unggulan = models.BooleanField(default=False)
    kategori_sale = models.BooleanField(default=False)
    kategori_preorder = models.BooleanField(default=False)
    kategori_rilis_terbaru = models.BooleanField(default=False)
    kategori_populer = models.BooleanField(default=False)
    kategori_bahan_mentah = models.BooleanField(default=False)

    material = models.CharField(max_length=255, blank=True, null=True)
    berat = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Berat produk (kg)")
    deskripsi = models.TextField(blank=True, null=True)
    catatan = models.TextField(blank=True, default='')
    meta_keywords = models.CharField(max_length=255, blank=True, default='')
    meta_description = models.TextField(blank=True, default='')
    related_product_ids = models.JSONField(default=list, blank=True, help_text="List of related product IDs")
    serial_numbers = models.JSONField(default=list, blank=True, help_text="List of serial numbers: [{'id': '1', 'variant': 'All', 'no_seri': '1234'}]")
    uom_enabled = models.BooleanField(default=False)
    uom_settings = models.JSONField(default=dict, blank=True)
    uom_units = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nama

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='fotos')
    foto = models.ImageField(upload_to='product_photos/')
    is_primary = models.BooleanField(default=False)

class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    nama_varian = models.CharField(max_length=255)
    nama_alternatif = models.CharField(max_length=255, blank=True, default='')
    sku = models.CharField(max_length=100, blank=True, null=True)
    barcode = models.CharField(max_length=100, blank=True, null=True)
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_pasar = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_jual_online = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_jual_toko = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    lacak_inventori = models.BooleanField(default=True)
    qty_stok = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    rack = models.CharField(max_length=100, blank=True, default='', help_text="Lokasi rak penyimpanan khusus varian ini")
    berat = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Berat barang (gram), opsional")
    foto = models.ImageField(upload_to='variant_photos/', blank=True, null=True)
    loyalty_points = models.IntegerField(default=0)
    komisi = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    komisi_is_persen = models.BooleanField(default=False)
    habis_stok = models.BooleanField(default=False)
    pilihan_default = models.BooleanField(default=False)
    qty_fast_moving = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.product.nama} - {self.nama_varian}"

class ProductPackage(models.Model):
    nama = models.CharField(max_length=255)
    deskripsi = models.TextField(blank=True, null=True)
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="purchase_price")
    harga_pasar = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="market_price")
    harga_jual_offline = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    harga_jual_online = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    komisi = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="commission (Rp, bukan persen)")
    minimal_pesanan = models.IntegerField(default=1, help_text="minimum_order")
    maksimal_pesanan = models.IntegerField(default=0, help_text="maximum_order; 0 = tidak dibatasi")
    harga_dinamis = models.BooleanField(default=False, help_text="selling_prices_stores_are_dynamic")
    publikasi = models.BooleanField(default=True, help_text="ready_publish_sale")
    periode_mulai = models.DateTimeField(blank=True, null=True, help_text="sale_start_date")
    periode_selesai = models.DateTimeField(blank=True, null=True)
    loyalty_points = models.IntegerField(default=0)
    satuan = models.CharField(max_length=50, blank=True, default='', help_text="uom")
    butuh_pengiriman = models.BooleanField(default=True)
    bebas_pajak = models.BooleanField(default=False)
    bebas_biaya_layanan = models.BooleanField(default=False)
    tampil_pos = models.BooleanField(default=True)
    habis_stok = models.BooleanField(default=False)
    seo_keywords = models.CharField(max_length=255, blank=True, null=True)
    seo_description = models.TextField(blank=True, null=True)
    foto = models.ImageField(upload_to='package_photos/', blank=True, null=True)

    def __str__(self):
        return self.nama

class ProductPackageItem(models.Model):
    paket = models.ForeignKey(ProductPackage, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True)
    qty = models.IntegerField(default=1)

class Addon(models.Model):
    nama = models.CharField(max_length=255)
    harga = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)
    applies_to = models.ManyToManyField(Product, blank=True, related_name='addons')
    applies_to_categories = models.ManyToManyField(ProductCategory, blank=True, related_name='addons')
    linked_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='addon_stok_links')
    linked_variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='addon_stok_links')
    linked_qty = models.DecimalField(max_digits=10, decimal_places=2, default=1.00)

    def __str__(self):
        return self.nama

class Specification(models.Model):
    nama = models.CharField(max_length=255)
    tipe = models.CharField(max_length=50, blank=True, null=True)
    satuan = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return self.nama

class ProductSpecValue(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='specifications')
    specification = models.ForeignKey(Specification, on_delete=models.CASCADE)
    value = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.product.nama} - {self.specification.nama}: {self.value}"

class ProductStockMovement(models.Model):
    """Riwayat stok masuk/keluar/opname untuk Product (setara RestockHistory milik InventoryItem lama)."""
    TIPE_CHOICES = [
        ('masuk', 'Stok Masuk'),
        ('keluar', 'Stok Keluar'),
        ('opname', 'Stok Opname'),
        ('produksi', 'Produksi Stok'),
        ('penjualan', 'Penjualan'),
        ('pengembalian', 'Pengembalian'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_movements')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='stock_movements')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='product_stock_movements')

    tipe = models.CharField(max_length=20, choices=TIPE_CHOICES)
    qty = models.DecimalField(max_digits=10, decimal_places=2, help_text="Selalu positif; arah ditentukan oleh 'tipe'")
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Harga beli per unit saat stok masuk")
    stok_awal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    stok_akhir = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    catatan = models.TextField(blank=True, default='')
    tanggal = models.DateField(null=True, blank=True, help_text="Tanggal dokumen (boleh beda dari waktu input)")
    stock_in_document = models.ForeignKey('StockInDocument', on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    stock_out_document = models.ForeignKey('StockOutDocument', on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    stock_production_document = models.ForeignKey('StockProductionDocument', on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    stock_opname_document = models.ForeignKey('StockOpnameDocument', on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', '-created_at'], name='idx_prod_stockmv_prod_time'),
        ]

    def __str__(self):
        return f"{self.product.nama} {self.tipe} {self.qty} ({self.created_at:%Y-%m-%d})"

class StockInDocument(models.Model):
    """Dokumen Stok Masuk (header + banyak item), setara fitur 'Stok Masuk' Olsera."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('selesai', 'Selesai'),
        ('batal', 'Batal'),
    ]

    nomor = models.CharField(max_length=50, unique=True, blank=True)
    tanggal = models.DateField()
    catatan = models.TextField(blank=True, default='')
    nama_penerima = models.CharField(max_length=255, blank=True, default='', help_text="Nama staf yang menerima barang (Info Penerimaan di Olsera)")
    supplier = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_in_documents')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nomor or f"StockIn-{self.pk}"

class StockInDocumentItem(models.Model):
    document = models.ForeignKey(StockInDocument, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_in_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='stock_in_items')
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    rak = models.CharField(max_length=100, blank=True, default='', help_text="Lokasi rak/gudang (kolom 'rack' di template import Olsera)")

    def __str__(self):
        return f"{self.document.nomor} - {self.product.nama} x{self.qty}"


class StockOutDocument(models.Model):
    """Dokumen Stok Keluar (header + banyak item), setara fitur 'Stok Keluar' Olsera."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('selesai', 'Selesai'),
        ('batal', 'Batal'),
    ]
    REASON_CHOICES = [
        # 'manual' & 'transfer' dipertahankan: sudah dipakai dokumen lama, dan
        # 'transfer' adalah alur tersendiri (transfer_ke / to_store_url_id di
        # Olsera). Keduanya sengaja TIDAK ditawarkan di dropdown alasan.
        ('manual', 'Manual'),
        ('transfer', 'Transfer Toko'),
        # Daftar alasan Olsera, sesuai urutan di layarnya.
        ('rusak', 'Rusak'),
        ('kadaluarsa', 'Kadaluarsa'),
        ('refund', 'Pengembalian dana (refund)'),
        ('kelebihan_stok', 'Jumlah stock kelebihan'),
        ('lainnya', 'Alasan lainnya'),
    ]

    nomor = models.CharField(max_length=50, unique=True, blank=True)
    tanggal = models.DateField()
    catatan = models.TextField(blank=True, default='')
    alasan = models.CharField(max_length=20, choices=REASON_CHOICES, default='manual')
    alasan_lainnya = models.CharField(max_length=255, blank=True, default='', help_text="Teks bebas; wajib diisi saat alasan='lainnya', diabaikan untuk alasan lain")
    transfer_ke = models.CharField(max_length=255, blank=True, default='', help_text="Tujuan transfer toko (to_store_url_id di Olsera)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_out_documents')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nomor or f"StockOut-{self.pk}"


class StockOutDocumentItem(models.Model):
    document = models.ForeignKey(StockOutDocument, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_out_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='stock_out_items')
    qty = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.document.nomor} - {self.product.nama} x{self.qty}"


class StockProductionDocument(models.Model):
    """Dokumen Produksi Stok (header + banyak item), setara fitur 'Produksi Stok' Olsera.
    Sesuai dokumentasi resmi: hanya menambah stok produk jadi (tanpa penyerapan bahan baku/BOM)."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('selesai', 'Selesai'),
        ('batal', 'Batal'),
    ]

    nomor = models.CharField(max_length=50, unique=True, blank=True)
    tanggal = models.DateField()
    catatan = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_production_documents')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nomor or f"StockProd-{self.pk}"


class StockProductionDocumentItem(models.Model):
    document = models.ForeignKey(StockProductionDocument, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_production_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='stock_production_items')
    qty = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.document.nomor} - {self.product.nama} x{self.qty}"

class StockOpnameDocument(models.Model):
    """Dokumen Stok Opname (header + banyak item), setara fitur 'Stok Opname' Olsera.
    Posting akan menimpa qty_stok produk dengan qty aktual hasil hitung fisik."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('selesai', 'Selesai'),
        ('batal', 'Batal'),
    ]

    nomor = models.CharField(max_length=50, unique=True, blank=True)
    tanggal = models.DateField()
    catatan = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_opname_documents')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nomor or f"StockOpname-{self.pk}"

class StockOpnameDocumentItem(models.Model):
    document = models.ForeignKey(StockOpnameDocument, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_opname_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='stock_opname_items')
    jam_opname = models.CharField(max_length=20, blank=True, default='', help_text="Jam hitung fisik dilakukan (dokumentasi saja)")
    rak = models.CharField(max_length=100, blank=True, default='', help_text="Lokasi rak (kolom 'rack' di template import Olsera)")
    tanggal_kadaluwarsa = models.DateField(null=True, blank=True, help_text="Tgl Kadaluwarsa (opsional, kolom di layar detail Olsera)")
    stok_sistem = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Snapshot qty_stok saat produk ditambahkan ke opname")
    stok_aktual = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Hasil hitung fisik (per baris/rak; produk sama bisa muncul >1 baris)")

    def __str__(self):
        return f"{self.document.nomor} - {self.product.nama} ({self.stok_sistem} -> {self.stok_aktual})"

class ProductActivityLog(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='activity_logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    aksi = models.CharField(max_length=255)
    catatan = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.nama} - {self.aksi} ({self.created_at:%Y-%m-%d %H:%M})"
