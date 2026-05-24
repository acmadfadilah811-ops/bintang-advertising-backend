# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0011_order_metode_pembayaran_orderitem_keterangan_detail_alter_order_waktu'),
    ]

    operations = [
        migrations.AddField(
            model_name='productprice',
            name='material',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='productprice',
            name='price_type',
            field=models.CharField(default='flat', max_length=20),
        ),
        migrations.AddField(
            model_name='productprice',
            name='tiers',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='productprice',
            name='harga',
            field=models.IntegerField(default=0),
        ),
    ]
