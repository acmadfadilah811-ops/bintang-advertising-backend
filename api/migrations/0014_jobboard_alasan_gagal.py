# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0013_jobboard_otp_code_jobboard_otp_requested'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobboard',
            name='alasan_gagal',
            field=models.TextField(blank=True, null=True, help_text="Alasan pengerjaan gagal atau dibatalkan"),
        ),
    ]
