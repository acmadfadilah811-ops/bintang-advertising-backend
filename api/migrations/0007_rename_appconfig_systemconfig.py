# Migration: Rename AppConfig → SystemConfig
# Dibuat manual karena model sudah diganti nama tapi migration belum dibuat.
# Ini akan rename tabel di MySQL dari api_appconfig → api_systemconfig.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0006_contact_keterangan'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='AppConfig',
            new_name='SystemConfig',
        ),
    ]
