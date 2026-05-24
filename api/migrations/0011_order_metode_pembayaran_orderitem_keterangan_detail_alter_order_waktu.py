# Generated manually

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0010_order_diskon_persen_order_dp_dibayar_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='metode_pembayaran',
            field=models.CharField(default='tunai', max_length=20),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='keterangan_detail',
            field=models.TextField(blank=True, help_text='Keterangan khusus/detail cetak dari CS', null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='waktu',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
