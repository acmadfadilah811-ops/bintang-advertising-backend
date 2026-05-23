from django.db import migrations, models

def clean_last_order(apps, schema_editor):
    # Bersihkan string aneh (seperti '-') menjadi NULL sebelum di-alter menjadi DATE
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("UPDATE api_contact SET last_order = NULL WHERE last_order = '-' OR last_order = '';")
        # Jika ada datetime format '%Y-%m-%d %H:%M', MySQL's ALTER TABLE ke DATE akan memotongnya menjadi %Y-%m-%d secara otomatis.

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0007_rename_appconfig_systemconfig'),
    ]

    operations = [
        migrations.RunPython(clean_last_order),
        migrations.AlterField(
            model_name='contact',
            name='last_order',
            field=models.DateField(blank=True, null=True),
        ),
    ]
