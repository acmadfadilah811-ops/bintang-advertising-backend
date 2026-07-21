from django.db import migrations, models
import django.db.models.deletion

PASSKEYS = (
    'pos_passkey_diskon_val', 'pos_passkey_pelanggan_val',
    'pos_passkey_belum_bayar_val', 'pos_passkey_sudah_bayar_val',
)

def hash_legacy_passkeys(apps, schema_editor):
    from django.contrib.auth.hashers import identify_hasher, make_password
    Config = apps.get_model('api', 'SystemConfig')
    for key in PASSKEYS:
        obj = Config.objects.filter(key=key).first()
        if not obj or not obj.value:
            continue
        try:
            identify_hasher(obj.value)
        except ValueError:
            obj.value = make_password(obj.value)
            obj.save(update_fields=['value'])

class Migration(migrations.Migration):
    dependencies = [('api', '0076_jobboard_pos_sale_item_alter_jobboard_order_item_and_more')]
    operations = [
        migrations.AlterField(model_name='saldokasharian', name='kas_awal', field=models.DecimalField(decimal_places=2, default=0, max_digits=15)),
        migrations.AlterField(model_name='saldokasharian', name='kas_akhir', field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
        migrations.AlterField(model_name='ringkasanshift', name='expected', field=models.DecimalField(decimal_places=2, default=0, max_digits=15)),
        migrations.AlterField(model_name='ringkasanshift', name='aktual', field=models.DecimalField(decimal_places=2, default=0, max_digits=15)),
        migrations.AlterField(model_name='ringkasanshift', name='selisih', field=models.DecimalField(decimal_places=2, default=0, max_digits=15)),
        migrations.AddField(model_name='cashtransaction', name='shift', field=models.ForeignKey(blank=True, help_text='Shift kas yang menerima transaksi ini', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cash_transactions', to='api.saldokasharian')),
        migrations.AddIndex(model_name='order', index=models.Index(fields=['status_global', '-waktu'], name='idx_order_status_time')),
        migrations.AddIndex(model_name='order', index=models.Index(fields=['sumber', 'status_global', '-waktu'], name='idx_order_src_status_time')),
        migrations.AddIndex(model_name='order', index=models.Index(fields=['nomor_wa', '-waktu'], name='idx_order_wa_time')),
        migrations.AddIndex(model_name='productstockmovement', index=models.Index(fields=['product', 'variant', '-tanggal'], name='idx_stockmv_prod_var_date')),
        migrations.AddIndex(model_name='productstockmovement', index=models.Index(fields=['tanggal', 'tipe'], name='idx_stockmv_date_type')),
        migrations.AddIndex(model_name='cashtransaction', index=models.Index(fields=['arah', '-waktu'], name='idx_cash_direction_time')),
        migrations.AddIndex(model_name='cashtransaction', index=models.Index(fields=['tipe_transaksi', '-waktu'], name='idx_cash_type_time')),
        migrations.AddIndex(model_name='cashtransaction', index=models.Index(fields=['staff', '-waktu'], name='idx_cash_staff_time')),
        migrations.RunPython(hash_legacy_passkeys, migrations.RunPython.noop),
    ]
