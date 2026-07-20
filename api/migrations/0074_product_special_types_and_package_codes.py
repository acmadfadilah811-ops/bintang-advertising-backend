from django.db import migrations, models


DEFAULT_TYPES = [
    ('Unggulan', 'unggulan', 'lightbulb'),
    ('Rilis Terbaru', 'rilis-terbaru', 'compass'),
    ('Sale', 'sale', 'tag'),
    ('Populer', 'populer', 'trending-up'),
    ('Habis Stok', 'habis-stok', 'ban'),
    ('Pre-order', 'pre-order', 'calendar'),
    ('Bahan Baku', 'bahan-baku', 'droplet'),
]


def seed_and_backfill(apps, schema_editor):
    SpecialType = apps.get_model('api', 'SpecialType')
    Product = apps.get_model('api', 'Product')
    for urutan, (nama, key, icon) in enumerate(DEFAULT_TYPES, start=1):
        SpecialType.objects.get_or_create(
            key=key,
            defaults={'nama': nama, 'icon': icon, 'urutan': urutan},
        )
    for product in Product.objects.exclude(tipe_special_id=None).iterator():
        product.tipe_specials.add(product.tipe_special_id)


class Migration(migrations.Migration):
    dependencies = [('api', '0073_stockoutdocumentitem_uom_kode_and_more')]

    operations = [
        migrations.AddField(
            model_name='product',
            name='tipe_specials',
            field=models.ManyToManyField(blank=True, related_name='products_multi', to='api.specialtype'),
        ),
        migrations.AddField(
            model_name='productpackage',
            name='sku',
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='productpackage',
            name='barcode',
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
        migrations.RunPython(seed_and_backfill, migrations.RunPython.noop),
    ]
