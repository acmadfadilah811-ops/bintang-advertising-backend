import os
import shutil
import subprocess
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

class Command(BaseCommand):
    help = 'Melakukan backup database secara otomatis (Mendukung SQLite dan MySQL) dengan retensi 30 hari'

    def handle(self, *args, **options):
        db_config = settings.DATABASES['default']
        engine = db_config['ENGINE']
        
        # Buat direktori backup di dalam workspace
        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if 'sqlite3' in engine:
            db_path = db_config['NAME']
            if not os.path.exists(db_path):
                self.stdout.write(self.style.ERROR(f'Database SQLite tidak ditemukan di {db_path}'))
                return
            
            backup_file = os.path.join(backup_dir, f'backup_sqlite_{timestamp}.db')
            shutil.copy2(db_path, backup_file)
            self.stdout.write(self.style.SUCCESS(f'✅ Berhasil membackup database SQLite ke: {backup_file}'))
            
        elif 'mysql' in engine:
            db_name = db_config['NAME']
            db_user = db_config['USER']
            db_password = db_config['PASSWORD']
            db_host = db_config.get('HOST', '127.0.0.1')
            db_port = db_config.get('PORT', '3306')
            
            backup_file = os.path.join(backup_dir, f'backup_mysql_{timestamp}.sql')
            
            # Susun command mysqldump
            cmd = [
                'mysqldump',
                f'-h{db_host}',
                f'-P{db_port}',
                f'-u{db_user}',
            ]
            if db_password:
                cmd.append(f'-p{db_password}')
            cmd.append(db_name)
            
            try:
                with open(backup_file, 'w', encoding='utf-8') as f:
                    subprocess.run(cmd, stdout=f, check=True, stderr=subprocess.PIPE)
                
                # Coba kompres ke .gz jika gzip tersedia, jika tidak biarkan berkas .sql
                try:
                    import gzip
                    compressed_file = backup_file + '.gz'
                    with open(backup_file, 'rb') as f_in:
                        with gzip.open(compressed_file, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    os.remove(backup_file)
                    backup_file = compressed_file
                    self.stdout.write(self.style.SUCCESS(f'✅ Berhasil membackup database MySQL (Gzipped) ke: {backup_file}'))
                except Exception:
                    self.stdout.write(self.style.SUCCESS(f'✅ Berhasil membackup database MySQL ke: {backup_file}'))
                    
            except FileNotFoundError:
                self.stdout.write(self.style.ERROR(
                    '❌ Gagal melakukan backup: Command `mysqldump` tidak ditemukan di system PATH.\n'
                    '👉 Pastikan MySQL Client / mysqldump sudah terinstal dan terdaftar di Environment Variables Anda.'
                ))
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                return
            except subprocess.CalledProcessError as e:
                self.stdout.write(self.style.ERROR(f'❌ Gagal melakukan mysqldump: {e.stderr.decode().strip()}'))
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                return
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Terjadi kesalahan saat membackup MySQL: {str(e)}'))
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                return
        
        # --- Retensi Pembersihan Otomatis (Hapus backup > 30 hari) ---
        cutoff = datetime.now() - timedelta(days=30)
        deleted_count = 0
        
        for filename in os.listdir(backup_dir):
            file_path = os.path.join(backup_dir, filename)
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if file_time < cutoff:
                    os.remove(file_path)
                    deleted_count += 1
                    
        if deleted_count > 0:
            self.stdout.write(self.style.WARNING(f'🧹 Retensi 30 Hari: Berhasil menghapus {deleted_count} file backup usang.'))
