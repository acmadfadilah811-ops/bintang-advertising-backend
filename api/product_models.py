from decimal import Decimal

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
    # Satu produk dapat tampil di beberapa daftar khusus sekaligus (misalnya
    # Unggulan dan Sale). FK lama dipertahankan agar data/klien lama tetap
    # kompatibel, sedangkan fitur baru memakai relasi many-to-many ini.
    tipe_specials = models.ManyToManyField(SpecialType, blank=True, related_name='products_multi')

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
    sku = models.CharField(max_length=100, blank=True, null=True, unique=True)
    barcode = models.CharField(max_length=100, blank=True, null=True, unique=True)
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
    hpp_total = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="HPP mutasi keluar, dihitung dari lapisan FIFO yang dikonsumsi",
    )
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
    nama_penerima = models.CharField(max_length=255, blank=True, default='', help_text="Nama staf yang menerima barang")
    supplier = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_in_documents')
    # Tautan ke dokumen Pembelian bila stok-masuk ini lahir dari penerimaan PO.
    # Null untuk stok-masuk manual biasa (menu Inventory).
    purchase = models.ForeignKey('Purchase', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_in_documents')
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
    rak = models.CharField(max_length=100, blank=True, default='', help_text="Lokasi rak/gudang (kolom 'rack' di template import)")
    tanggal_kadaluwarsa = models.DateField(null=True, blank=True, help_text="Dipakai pada mode stok 'FIFO & Expired'")
    # Satuan alternatif (UOM). `qty` SELALU dalam satuan dasar; field di bawah
    # hanya merekam bagaimana angka itu diinput agar bisa ditampilkan kembali.
    uom_kode = models.CharField(max_length=10, blank=True, default='')
    uom_konverter = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    uom_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Qty sesuai satuan yang dipilih")

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
    transfer_ke = models.CharField(max_length=255, blank=True, default='', help_text="Tujuan transfer toko (kolom 'to_store_url_id' di template import)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_out_documents')
    # Tautan ke dokumen Pembelian bila stok-keluar ini lahir dari posting Retur Pembelian.
    purchase = models.ForeignKey('Purchase', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_out_documents')
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
    qty = models.DecimalField(max_digits=10, decimal_places=2, help_text="Qty dalam satuan DASAR")
    # Satuan alternatif (UOM) yang dipilih saat input; qty di atas tetap basis dasar.
    uom_kode = models.CharField(max_length=10, blank=True, default='')
    uom_konverter = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    uom_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

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
    # Default False supaya perilaku dokumen lama tidak berubah: sebelum fitur ini
    # ada, biaya produksi memang tidak pernah masuk HPP. Lihat
    # StockProductionDocumentViewSet.post_document untuk cara alokasinya.
    serap_biaya_ke_hpp = models.BooleanField(
        default=False,
        help_text='Bila aktif, total biaya produksi dibebankan ke HPP barang jadi '
                  'secara proporsional terhadap nilai bahan tiap item saat posting.',
    )
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
    rak = models.CharField(max_length=100, blank=True, default='', help_text="Lokasi rak (kolom 'rack' di template import)")
    tanggal_kadaluwarsa = models.DateField(null=True, blank=True, help_text="Tgl Kadaluwarsa (opsional)")
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


# ---------------------------------------------------------------------------
# Pembelian (Purchase Order) — modul mandiri, terpisah dari Stok Masuk.
# Mengikuti poin penting Olsera: dua dimensi status independen (pembayaran &
# penerimaan), stok baru bertambah saat penerimaan diposting, dan retur benar-
# benar mengurangi stok. PO dan retur disatukan di model ini via flag is_retur
# supaya satu endpoint memberi makan keempat tab layar Pembelian.
# ---------------------------------------------------------------------------
class Purchase(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),        # Butuh Diproses
        ('selesai', 'Selesai'),    # Telah Diproses / retur terposting
        ('batal', 'Batal'),        # Dibatalkan
    ]
    RECEIVE_CHOICES = [
        ('tunda', 'Tunda'),        # barang belum sampai
        ('diterima', 'Diterima'),  # barang sampai -> stok bertambah
    ]
    PAYMENT_CHOICES = [
        ('belum', 'Belum Bayar'),
        ('sebagian', 'Sebagian'),  # DP / cicilan
        ('lunas', 'Lunas'),
    ]

    nomor = models.CharField(max_length=50, unique=True, blank=True)
    tanggal = models.DateField(help_text="Tanggal beli / pembuatan PO")
    mata_uang = models.CharField(max_length=10, blank=True, default='IDR')
    catatan = models.TextField(blank=True, default='')
    jatuh_tempo = models.DateField(null=True, blank=True, help_text="Jatuh tempo pembayaran (opsional)")

    # Supplier: FK ke master bila ada, plus nama teks untuk back-compat / import.
    supplier = models.CharField(max_length=255, blank=True, default='')
    supplier_ref = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchases')

    # Dimensi 1: penerimaan barang
    receive_status = models.CharField(max_length=20, choices=RECEIVE_CHOICES, default='tunda')
    tanggal_diterima = models.DateField(null=True, blank=True)
    no_terima = models.CharField(max_length=100, blank=True, default='')
    lanjut_tambah_stok = models.BooleanField(default=True, help_text="Bila True, penerimaan menambah stok (buat Stok Masuk)")

    # Dimensi 2: pembayaran (diturunkan dari PurchasePayment)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='belum')

    # Retur
    is_retur = models.BooleanField(default=False)
    retur_ref = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='returns')
    exchange_new = models.BooleanField(default=False, help_text="Retur ditukar barang baru (stok ditambah kembali)")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchases')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nomor or f"Purchase-{self.pk}"

    @property
    def total(self):
        return sum((it.qty * it.harga_beli for it in self.items.all()), start=Decimal('0'))

    @property
    def total_dibayar(self):
        return sum((p.nominal for p in self.payments.all()), start=Decimal('0'))

    def recompute_payment_status(self):
        """Selaraskan payment_status dari akumulasi pembayaran. Panggil setelah
        pembayaran ditambah/dihapus.

        Memakai agregasi DB (bukan properti .total/.total_dibayar) supaya kebal
        terhadap cache prefetch_related yang mungkin sudah basi pada instance ini."""
        from django.db.models import Sum, F, DecimalField
        total = self.items.aggregate(
            t=Sum(F('qty') * F('harga_beli'), output_field=DecimalField())
        )['t'] or Decimal('0')
        dibayar = self.payments.aggregate(t=Sum('nominal'))['t'] or Decimal('0')
        if dibayar <= 0:
            self.payment_status = 'belum'
        elif dibayar >= total and total > 0:
            self.payment_status = 'lunas'
        else:
            self.payment_status = 'sebagian'
        self.save(update_fields=['payment_status', 'updated_at'])


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='purchase_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='purchase_items')
    qty = models.DecimalField(max_digits=10, decimal_places=2)
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    tanggal_kadaluwarsa = models.DateField(null=True, blank=True, help_text="Dipakai pada mode stok 'FIFO & Expired'")
    # Satuan alternatif (UOM). `qty` & `harga_beli` SELALU dalam satuan dasar.
    uom_kode = models.CharField(max_length=10, blank=True, default='')
    uom_konverter = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    uom_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Qty sesuai satuan yang dipilih")

    def __str__(self):
        return f"{self.purchase.nomor} - {self.product.nama} x{self.qty}"

    @property
    def subtotal(self):
        return self.qty * self.harga_beli


# ---------------------------------------------------------------------------
# Lapisan biaya stok (FIFO / FEFO)
#
# Setiap penerimaan barang membuat satu StockLayer. Setiap pengeluaran barang
# mengonsumsi lapisan tertua lebih dulu (FIFO), atau yang paling cepat
# kedaluwarsa lebih dulu bila mode stok = 'fifo_expired' (FEFO).
#
# Lapisan dipelihara di SEMUA mode stok — mode hanya menentukan apakah HPP
# diambil dari lapisan (FIFO) atau dari Product.harga_beli (average). Dengan
# begitu perpindahan mode tidak menghilangkan riwayat.
# ---------------------------------------------------------------------------
class StockLayer(models.Model):
    SUMBER_CHOICES = [
        ('saldo_awal', 'Saldo Awal (sync)'),
        ('stock_in', 'Stok Masuk'),
        ('purchase', 'Pembelian'),
        ('produksi', 'Produksi'),
        ('opname', 'Penyesuaian Opname'),
        ('pos_void', 'Pembatalan POS'),
        ('manual', 'Manual'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_layers')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='stock_layers')

    tanggal_masuk = models.DateField(db_index=True)
    qty_masuk = models.DecimalField(max_digits=12, decimal_places=2)
    sisa_qty = models.DecimalField(max_digits=12, decimal_places=2, help_text="Sisa yang belum terpakai")
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Biaya per unit lapisan ini")
    tanggal_kadaluwarsa = models.DateField(null=True, blank=True, db_index=True)
    rak = models.CharField(max_length=100, blank=True, default='')

    sumber_tipe = models.CharField(max_length=20, choices=SUMBER_CHOICES, default='manual')
    sumber_nomor = models.CharField(max_length=50, blank=True, default='', help_text="Nomor dokumen asal, untuk laporan")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tanggal_masuk', 'id']
        indexes = [
            models.Index(fields=['product', 'variant', 'sisa_qty'], name='idx_layer_prod_var_sisa'),
        ]

    def __str__(self):
        return f"{self.product.nama} {self.tanggal_masuk} sisa {self.sisa_qty}@{self.harga_beli}"


class StockLayerConsumption(models.Model):
    """Jejak audit: berapa qty diambil dari lapisan mana, dengan biaya berapa.

    `layer` null berarti pengambilan melebihi lapisan yang tersedia (data drift /
    stok minus) dan memakai harga beli produk sebagai fallback.
    """
    layer = models.ForeignKey(StockLayer, on_delete=models.SET_NULL, null=True, blank=True, related_name='consumptions')
    movement = models.ForeignKey(ProductStockMovement, on_delete=models.CASCADE, null=True, blank=True, related_name='layer_consumptions')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='layer_consumptions')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name='layer_consumptions')
    qty = models.DecimalField(max_digits=12, decimal_places=2)
    harga_beli = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    is_shortfall = models.BooleanField(default=False, help_text="True bila lapisan tidak mencukupi")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.nama} -{self.qty} @{self.harga_beli}"


class PurchasePayment(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='payments')
    tanggal = models.DateField()
    nominal = models.DecimalField(max_digits=14, decimal_places=2)
    metode = models.CharField(max_length=50, blank=True, default='')
    catatan = models.CharField(max_length=255, blank=True, default='')
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_payments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tanggal', 'created_at']

    def __str__(self):
        return f"{self.purchase.nomor} - {self.nominal} ({self.tanggal})"
