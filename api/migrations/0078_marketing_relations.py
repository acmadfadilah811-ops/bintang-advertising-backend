"""Sambungkan modul marketing ke data produk/pelanggan yang sesungguhnya.

Sebelum ini kriteria promo disimpan sebagai CharField berisi nama dipisah koma:
tidak ada integritas referensial, dan `max_length=255` memotong pilihan tanpa
peringatan. Migrasi ini menambah relasi M2M, lalu memetakan nama lama ke objek.

Kolom lama TIDAK dihapus — hanya diberi akhiran `_legacy`. Pemetaan berbasis
nama tidak bisa dijamin 100% (nama berubah, duplikat, typo), jadi datanya
disimpan sampai hasilnya diverifikasi di produksi. Nama yang tidak ketemu
dicatat sebagai WARNING, bukan dibuang diam-diam.
"""
import logging

import django.db.models.deletion
from django.db import migrations, models

logger = logging.getLogger(__name__)


def _names(raw):
    """Pecah 'A, B , C' menjadi ['A', 'B', 'C'] tanpa entri kosong."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(',') if part.strip()]


def _resolve(model, names, label, ident):
    """Petakan daftar nama ke objek; catat yang tidak ketemu/ambigu."""
    found = []
    for name in names:
        matches = list(model.objects.filter(nama__iexact=name)[:2])
        if not matches:
            logger.warning("[0078] %s %s: '%s' tidak ditemukan di %s — dilewati.",
                           label, ident, name, model.__name__)
            continue
        if len(matches) > 1:
            logger.warning("[0078] %s %s: '%s' cocok >1 %s — memakai id=%s.",
                           label, ident, name, model.__name__, matches[0].pk)
        found.append(matches[0])
    return found


def petakan_nama_ke_relasi(apps, schema_editor):
    Product = apps.get_model('api', 'Product')
    ProductCategory = apps.get_model('api', 'ProductCategory')
    ProductPackage = apps.get_model('api', 'ProductPackage')
    Brand = apps.get_model('api', 'Brand')
    Contact = apps.get_model('api', 'Contact')

    SalesDiscount = apps.get_model('api', 'SalesDiscount')
    DiscountCoupon = apps.get_model('api', 'DiscountCoupon')
    POSPromotion = apps.get_model('api', 'POSPromotion')

    for sd in SalesDiscount.objects.all():
        objs = _resolve(Brand, _names(sd.brand_legacy), 'SalesDiscount', sd.pk)
        if objs:
            sd.brand.set(objs)

    for cp in DiscountCoupon.objects.all():
        ident = cp.kode
        for legacy, rel, model in (
            (cp.produk_legacy, cp.produk, Product),
            (cp.grup_produk_legacy, cp.grup_produk, ProductCategory),
            (cp.paket_produk_legacy, cp.paket_produk, ProductPackage),
            (cp.brand_legacy, cp.brand, Brand),
        ):
            objs = _resolve(model, _names(legacy), 'DiscountCoupon', ident)
            if objs:
                rel.set(objs)
        # Contact dikunci pada `nama`, bukan pk (pk-nya nomor_wa).
        objs = _resolve(Contact, _names(cp.pelanggan_legacy), 'DiscountCoupon', ident)
        if objs:
            cp.pelanggan.set(objs)

    for pr in POSPromotion.objects.all():
        ident = pr.judul
        for legacy, rel, model in (
            (pr.grup_produk_legacy, pr.grup_produk, ProductCategory),
            (pr.paket_produk_legacy, pr.paket_produk, ProductPackage),
            (pr.brand_legacy, pr.brand, Brand),
            (pr.produk_gratis_legacy, pr.produk_gratis, Product),
        ):
            objs = _resolve(model, _names(legacy), 'POSPromotion', ident)
            if objs:
                rel.set(objs)
        objs = _resolve(Contact, _names(pr.pelanggan_legacy), 'POSPromotion', ident)
        if objs:
            pr.pelanggan.set(objs)

        # produk_qty: [{"nama","qty"}] -> tambahkan product_id agar pencocokan
        # tidak lagi bergantung pada nama.
        entries = pr.produk_qty or []
        if isinstance(entries, list):
            ubah = False
            for entry in entries:
                if not isinstance(entry, dict) or entry.get('product_id'):
                    continue
                match = _resolve(Product, [entry.get('nama') or ''], 'POSPromotion.produk_qty', ident)
                entry['product_id'] = match[0].pk if match else None
                ubah = True
            if ubah:
                pr.produk_qty = entries
                pr.save(update_fields=['produk_qty'])


def kosongkan_relasi(apps, schema_editor):
    """Balik arah: kosongkan M2M. Kolom `_legacy` tidak pernah diubah, jadi
    data aslinya tetap utuh untuk dipetakan ulang."""
    for nama_model in ('SalesDiscount', 'DiscountCoupon', 'POSPromotion'):
        Model = apps.get_model('api', nama_model)
        for obj in Model.objects.all():
            for field in ('produk', 'grup_produk', 'paket_produk', 'brand', 'pelanggan', 'produk_gratis'):
                rel = getattr(obj, field, None)
                if rel is not None and hasattr(rel, 'clear'):
                    rel.clear()


class Migration(migrations.Migration):

    dependencies = [('api', '0077_audit_security_integrity')]

    operations = [
        # --- 1. Simpan kolom nama lama sebagai jejak audit -------------------
        migrations.RenameField('salesdiscount', 'brand', 'brand_legacy'),
        migrations.RenameField('discountcoupon', 'produk', 'produk_legacy'),
        migrations.RenameField('discountcoupon', 'grup_produk', 'grup_produk_legacy'),
        migrations.RenameField('discountcoupon', 'paket_produk', 'paket_produk_legacy'),
        migrations.RenameField('discountcoupon', 'brand', 'brand_legacy'),
        migrations.RenameField('discountcoupon', 'pelanggan', 'pelanggan_legacy'),
        migrations.RenameField('pospromotion', 'grup_produk', 'grup_produk_legacy'),
        migrations.RenameField('pospromotion', 'paket_produk', 'paket_produk_legacy'),
        migrations.RenameField('pospromotion', 'brand', 'brand_legacy'),
        migrations.RenameField('pospromotion', 'produk_gratis', 'produk_gratis_legacy'),
        migrations.RenameField('pospromotion', 'pelanggan', 'pelanggan_legacy'),

        # --- 2. Selaraskan help_text kolom legacy ---------------------------
        migrations.AlterField('salesdiscount', 'brand_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama brand dipisah koma; dipetakan ke relasi `brand` oleh migrasi 0078.', max_length=255)),
        migrations.AlterField('discountcoupon', 'produk_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama produk dipisah koma.', max_length=255)),
        migrations.AlterField('discountcoupon', 'grup_produk_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama grup produk dipisah koma.', max_length=255)),
        migrations.AlterField('discountcoupon', 'paket_produk_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama paket dipisah koma.', max_length=255)),
        migrations.AlterField('discountcoupon', 'brand_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama brand dipisah koma.', max_length=255)),
        migrations.AlterField('discountcoupon', 'pelanggan_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama pelanggan dipisah koma.', max_length=255)),
        migrations.AlterField('discountcoupon', 'penggunaan_count', models.IntegerField(default=0, help_text='Diperbarui oleh promo_engine; sumber kebenarannya tetap tabel CouponUsage.')),
        migrations.AlterField('pospromotion', 'grup_produk_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama grup produk dipisah koma.', max_length=255)),
        migrations.AlterField('pospromotion', 'paket_produk_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama paket dipisah koma.', max_length=255)),
        migrations.AlterField('pospromotion', 'brand_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama brand dipisah koma.', max_length=255)),
        migrations.AlterField('pospromotion', 'produk_gratis_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama produk gratis dipisah koma.', max_length=255)),
        migrations.AlterField('pospromotion', 'pelanggan_legacy', models.CharField(blank=True, default='', help_text='LEGACY nama pelanggan dipisah koma.', max_length=255)),

        # --- 3. Relasi sesungguhnya -----------------------------------------
        migrations.AddField('salesdiscount', 'brand', models.ManyToManyField(blank=True, related_name='sales_discounts', to='api.brand')),
        migrations.AddField('discountcoupon', 'produk', models.ManyToManyField(blank=True, related_name='discount_coupons', to='api.product')),
        migrations.AddField('discountcoupon', 'grup_produk', models.ManyToManyField(blank=True, related_name='discount_coupons', to='api.productcategory')),
        migrations.AddField('discountcoupon', 'paket_produk', models.ManyToManyField(blank=True, related_name='discount_coupons', to='api.productpackage')),
        migrations.AddField('discountcoupon', 'brand', models.ManyToManyField(blank=True, related_name='discount_coupons', to='api.brand')),
        migrations.AddField('discountcoupon', 'pelanggan', models.ManyToManyField(blank=True, related_name='discount_coupons', to='api.contact')),
        migrations.AddField('pospromotion', 'grup_produk', models.ManyToManyField(blank=True, related_name='pos_promotions', to='api.productcategory')),
        migrations.AddField('pospromotion', 'paket_produk', models.ManyToManyField(blank=True, related_name='pos_promotions', to='api.productpackage')),
        migrations.AddField('pospromotion', 'brand', models.ManyToManyField(blank=True, related_name='pos_promotions', to='api.brand')),
        migrations.AddField('pospromotion', 'produk_gratis', models.ManyToManyField(blank=True, related_name='pos_promotions_gratis', to='api.product')),
        migrations.AddField('pospromotion', 'pelanggan', models.ManyToManyField(blank=True, related_name='pos_promotions', to='api.contact')),

        # --- 4. Parameter promosi yang sebelumnya tidak punya tempat --------
        migrations.AddField('pospromotion', 'qty_gratis', models.IntegerField(default=1, help_text='Jumlah tiap produk gratis yang diberikan per pemicu (tipe BX/FI).')),
        migrations.AddField('pospromotion', 'min_total_transaksi', models.DecimalField(decimal_places=2, default=0, help_text='Ambang total transaksi untuk tipe DA.', max_digits=14)),
        migrations.AddField('pospromotion', 'tipe_diskon', models.CharField(choices=[('percent', 'Persen'), ('nominal', 'Nominal')], default='percent', help_text='Bentuk potongan untuk tipe DQ/DA.', max_length=10)),
        migrations.AddField('pospromotion', 'jumlah_diskon', models.DecimalField(decimal_places=2, default=0, help_text='Besaran potongan untuk tipe DQ/DA.', max_digits=14)),

        # --- 5. Jejak potongan di nota & pesanan ----------------------------
        migrations.AlterField('possale', 'diskon', models.DecimalField(decimal_places=2, default=0, help_text='Total potongan: manual + kupon + promosi.', max_digits=12)),
        migrations.AddField('possale', 'kupon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pos_sales', to='api.discountcoupon')),
        migrations.AddField('possale', 'diskon_manual', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField('possale', 'diskon_kupon', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField('possale', 'diskon_promo', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField('possaleitem', 'is_gratis', models.BooleanField(default=False)),
        migrations.AddField('possaleitem', 'promo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sale_items', to='api.pospromotion')),
        migrations.AddField('order', 'kupon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='api.discountcoupon')),
        migrations.AddField('order', 'diskon_kupon', models.IntegerField(default=0, help_text='Potongan nominal dari kupon')),
        migrations.AddField('order', 'diskon_otomatis', models.IntegerField(default=0, help_text='Potongan nominal dari Diskon Penjualan (Toko Online)')),

        # --- 6. Riwayat pemakaian kupon -------------------------------------
        migrations.CreateModel(
            name='CouponUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kanal', models.CharField(default='pos', max_length=10)),
                ('nilai_diskon', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('tanggal', models.DateField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('kupon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='usages', to='api.discountcoupon')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='coupon_usages', to='api.order')),
                ('pelanggan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coupon_usages', to='api.contact')),
                ('pos_sale', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='coupon_usages', to='api.possale')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex('couponusage', models.Index(fields=['kupon', 'pelanggan'], name='idx_cpnusage_kupon_plg')),
        migrations.AddIndex('couponusage', models.Index(fields=['kupon', 'tanggal'], name='idx_cpnusage_kupon_tgl')),

        # --- 7. Pindahkan datanya -------------------------------------------
        migrations.RunPython(petakan_nama_ke_relasi, kosongkan_relasi),
    ]
