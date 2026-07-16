from django.db import migrations, models


def migrate_produk_to_qty(apps, schema_editor):
    POSPromotion = apps.get_model('api', 'POSPromotion')
    for promo in POSPromotion.objects.all():
        if promo.produk:
            names = [n.strip() for n in promo.produk.split(',') if n.strip()]
            promo.produk_qty = [{"nama": name, "qty": 1} for name in names]
        else:
            promo.produk_qty = []
        promo.save()


def migrate_qty_to_produk(apps, schema_editor):
    POSPromotion = apps.get_model('api', 'POSPromotion')
    for promo in POSPromotion.objects.all():
        if promo.produk_qty:
            names = []
            for item in promo.produk_qty:
                if isinstance(item, dict) and 'nama' in item:
                    names.append(item['nama'])
            promo.produk = ", ".join(names)[:255]
        else:
            promo.produk = ""
        promo.save()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0063_stockoutdocument_alasan_lainnya_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pospromotion',
            name='produk_qty',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(migrate_produk_to_qty, migrate_qty_to_produk),
        migrations.RemoveField(
            model_name='pospromotion',
            name='produk',
        ),
    ]
