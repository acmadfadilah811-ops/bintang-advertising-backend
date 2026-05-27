# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0012_productprice_material_productprice_price_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobboard',
            name='otp_code',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
        migrations.AddField(
            model_name='jobboard',
            name='otp_requested',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='jobboard',
            name='otp_sent',
            field=models.BooleanField(default=False),
        ),
    ]
