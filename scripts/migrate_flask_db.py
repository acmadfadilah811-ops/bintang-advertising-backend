# ruff: noqa: E402, E701, E722
import os
import sys
import sqlite3
from datetime import datetime

# Setup Django Environment (Harus dijalankan di dalam folder bintang_django_backend)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django
django.setup()

from api.models import Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard, InventoryItem, ProductPrice, SystemConfig, FAQ

# Lokasi file database lama
OLD_DB_PATH = r"d:\buku zis\bintang_advertising_app\app.db"

def migrate_master_data():
    print("Membentuk Divisi dan Tahap Proses Default...")
    div_desain, _ = Divisi.objects.get_or_create(nama="Desain", keterangan="Tim Desain Grafis")
    div_cetak, _ = Divisi.objects.get_or_create(nama="Cetak", keterangan="Tim Cetak / Operator Mesin")
    div_finishing, _ = Divisi.objects.get_or_create(nama="Finishing", keterangan="Tim Finishing (Mata ayam, potong, dll)")
    
    TahapProses.objects.get_or_create(nama="Setting & Desain", divisi=div_desain, urutan=1)
    TahapProses.objects.get_or_create(nama="Cetak Mesin", divisi=div_cetak, urutan=2)
    TahapProses.objects.get_or_create(nama="Finishing & Packing", divisi=div_finishing, urutan=3)

def migrate_users(cursor):
    print("\nMigrasi Users...")
    cursor.execute("SELECT username, password_hash, role, is_active, nama_lengkap, email, no_hp, kota, negara, alamat, bio FROM user")
    users = cursor.fetchall()
    
    for u in users:
        username = u[0]
        if CustomUser.objects.filter(username=username).exists():
            continue
            
        role_map = {'admin': 'manager', 'karyawan': 'staff', 'produksi': 'staff'}
        new_role = role_map.get(u[2], 'staff')
        
        user = CustomUser(
            username=username,
            password=u[1], # Password hash bcrypt lama (mungkin butuh reset via admin nanti)
            is_active=bool(u[3]),
            first_name=u[4] or "",
            email=u[5] or "",
            no_hp=u[6] or "",
            kota=u[7] or "",
            negara=u[8] or "Indonesia",
            alamat=u[9] or "",
            bio=u[10] or "",
            role=new_role
        )
        user.save()
        print(f"  - User {username} sukses.")

def map_status_global(old_status):
    old_status = str(old_status).lower()
    if 'selesai' in old_status: return 'selesai'
    if 'batal' in old_status: return 'batal'
    if 'proses' in old_status or 'kerjakan' in old_status: return 'proses'
    return 'review'

def map_status_job(old_status):
    old_status = str(old_status).lower()
    if 'selesai' in old_status: return 'selesai'
    if 'batal' in old_status: return 'batal'
    if 'proses' in old_status or 'kerjakan' in old_status: return 'dikerjakan'
    return 'antrean'

def migrate_orders(cursor):
    print("\nMigrasi Pesanan (Memecah ke Order, OrderItem, dan JobBoard)...")
    
    # Ambil tahap pertama sebagai default JobBoard
    tahap_awal = TahapProses.objects.filter(urutan=1).first()
    
    # Cek apakah kolom gdrive_link ada di tabel lama (karena ini opsional di Flask)
    cursor.execute("PRAGMA table_info(pesanan)")
    columns = [col[1] for col in cursor.fetchall()]
    has_gdrive = 'gdrive_link' in columns
    
    if has_gdrive:
        query = "SELECT id, waktu, nama, nomor_wa, jenis_produk, detail, harga_jual, biaya_bahan, estimasi, status, tim_tugas, pic, catatan_produksi, gdrive_link FROM pesanan"
    else:
        query = "SELECT id, waktu, nama, nomor_wa, jenis_produk, detail, harga_jual, biaya_bahan, estimasi, status, tim_tugas, pic, catatan_produksi FROM pesanan"
    cursor.execute(query)
    pesanan = cursor.fetchall()
    
    for p in pesanan:
        oid = p[0]
        if Order.objects.filter(id=oid).exists():
            continue
            
        waktu_str = p[1]
        try:
            # Sesuaikan dengan format waktu Flask lama. Jika gagal, pakai waktu sekarang
            waktu_dt = datetime.strptime(waktu_str, "%Y-%m-%d %H:%M:%S")
        except:
            waktu_dt = datetime.now()

        # 1. Buat Induk Order
        order = Order.objects.create(
            id=oid,
            nama=p[2] or "Anonim",
            nomor_wa=p[3] or "-",
            status_global=map_status_global(p[9]),
            catatan_pelanggan=p[12] or ""
        )
        # Hack untuk mem-bypass auto_now_add
        Order.objects.filter(id=oid).update(waktu=waktu_dt)
        
        # 2. Buat Anak OrderItem
        detail_json = []
        if p[5]:
            detail_json.append({"keterangan": p[5]})
            
        glink = p[13] if has_gdrive and len(p) > 13 else ""
        
        item = OrderItem.objects.create(
            order=order,
            jenis_produk=p[4] or "Produk Umum",
            detail=detail_json,
            qty=1,
            harga_jual=p[6] or 0,
            biaya_bahan=p[7] or 0,
            estimasi=p[8] or "-",
            gdrive_customer_link=glink
        )
        
        # 3. Buat JobBoard History
        catatan_staff_json = []
        if p[12]:
            catatan_staff_json.append({"catatan_produksi_lama": p[12]})
            
        pic_user = CustomUser.objects.filter(username=p[11]).first() if p[11] else None
        
        JobBoard.objects.create(
            order_item=item,
            tahap=tahap_awal,
            pic_staff=pic_user,
            status_pekerjaan=map_status_job(p[9]),
            catatan_staff=catatan_staff_json
        )
        print(f"  - Order {oid} termigrasi (1 Item, 1 Job).")

def migrate_inventory(cursor):
    print("\nMigrasi Inventori...")
    cursor.execute("SELECT id, nama, stok, satuan, kategori, min_stok, cost_per_unit, supplier FROM inventory")
    for r in cursor.fetchall():
        if not InventoryItem.objects.filter(id=r[0]).exists():
            InventoryItem.objects.create(
                id=r[0], nama=r[1], stok=r[2], satuan=r[3], kategori=r[4],
                min_stok=r[5], cost_per_unit=r[6], supplier=r[7]
            )
            print(f"  - Inventori {r[1]} sukses.")

def migrate_contacts(cursor):
    print("\nMigrasi Kontak...")
    cursor.execute("SELECT nomor_wa, nama, total_order, total_spent, last_order FROM kontak")
    for r in cursor.fetchall():
        if not Contact.objects.filter(nomor_wa=r[0]).exists():
            Contact.objects.create(nomor_wa=r[0], nama=r[1], total_order=r[2], total_spent=r[3], last_order=r[4])

def migrate_others(cursor):
    print("\nMigrasi Harga, Config, dan FAQ...")
    cursor.execute("SELECT kategori, nama_produk, harga FROM harga")
    for r in cursor.fetchall():
        ProductPrice.objects.get_or_create(kategori=r[0], nama_produk=r[1], defaults={'harga': r[2]})
        
    cursor.execute("SELECT key, value FROM config")
    for r in cursor.fetchall():
        SystemConfig.objects.get_or_create(key=r[0], defaults={'value': r[1]})

    cursor.execute("SELECT pertanyaan, jawaban FROM faq")
    for r in cursor.fetchall():
        FAQ.objects.get_or_create(pertanyaan=r[0], defaults={'jawaban': r[1]})

def run():
    print("Memulai Proses Migrasi Data...")
    if not os.path.exists(OLD_DB_PATH):
        print(f"[ERROR] File database lama tidak ditemukan di {OLD_DB_PATH}")
        return

    from django.db import transaction

    conn = sqlite3.connect(OLD_DB_PATH)
    cursor = conn.cursor()

    try:
        with transaction.atomic():
            migrate_master_data()
            migrate_users(cursor)
            migrate_orders(cursor)
            migrate_inventory(cursor)
            migrate_contacts(cursor)
            migrate_others(cursor)
        print("\n[SUCCESS] SEMUA DATA BERHASIL DIMIGRASI KE MYSQL!")
    except Exception as e:
        print(f"\n[ERROR] Terjadi kesalahan: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    run()
