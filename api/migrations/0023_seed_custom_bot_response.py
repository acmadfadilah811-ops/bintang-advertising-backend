from django.db import migrations

def seed_custom_bot_response(apps, schema_editor):
    SystemConfig = apps.get_model('api', 'SystemConfig')
    SystemConfig.objects.update_or_create(
        key='custom_bot_response',
        defaults={'value': 'Selamat malam juga Kak! 😊 Saya adalah Cutomer Service AI, Ada yang bisa Bintang Advertising bantu terkait kebutuhan cetak Kakak?'}
    )

    # Also update system_prompt if it exists in the database to include Rule 5
    prompt_config = SystemConfig.objects.filter(key='system_prompt').first()
    if prompt_config:
        val = prompt_config.value
        if "INFORMASI TOTAL BIAYA" not in val:
            rule_text = (
                "\n5. INFORMASI TOTAL BIAYA: Setiap kali Anda memberikan estimasi total biaya atau total harga pesanan kepada pelanggan, Anda WAJIB menyertakan keterangan/catatan kaki berikut di bawah nominal harga:\n"
                "'*untuk harga tersebut belum termasuk biaya desain dan finishing ya kak, untuk rincian totalnya nanti akan di konfirmasi kembali dengan mengirimkan nota invoicenya kak😊'\n\n"
            )
            if "=== TEMPLATE FORM ORDER" in val:
                val = val.replace("=== TEMPLATE FORM ORDER", rule_text + "=== TEMPLATE FORM ORDER")
            else:
                val = val + "\n" + rule_text
            prompt_config.value = val
            prompt_config.save()

def remove_custom_bot_response(apps, schema_editor):
    SystemConfig = apps.get_model('api', 'SystemConfig')
    SystemConfig.objects.filter(key='custom_bot_response').delete()

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0022_orderitem_desain_susulan'),
    ]

    operations = [
        migrations.RunPython(seed_custom_bot_response, remove_custom_bot_response),
    ]
