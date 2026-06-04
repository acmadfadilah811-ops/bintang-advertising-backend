import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.models import CustomUser, Divisi

# 1. Pastikan Divisi ada
divisi_names = ['Editor', 'Operator', 'Finishing']
divisi_objs = {}
for name in divisi_names:
    obj, created = Divisi.objects.get_or_create(nama=name)
    divisi_objs[name] = obj
    if created:
        print(f"[SUCCESS] Divisi '{name}' berhasil dibuat!")

# 2. Definisikan 8 Akun
# Username, Email, Role, Divisi (Nama), Password
accounts = [
    # Owner & Manager
    ('owner_brandy', 'achmadfadhilah63@gmail.com', 'owner', None, 'BintangPassword123!'),
    ('manager_brandy', 'brandydesign14@gmail.com', 'manager', None, 'BintangPassword123!'),
    
    # Staff Divisi Editor (2 akun)
    ('staff_editor1', 'achmadfadhilah63@gmail.com', 'staff', 'Editor', 'BintangPassword123!'),
    ('staff_editor2', 'brandydesign14@gmail.com', 'staff', 'Editor', 'BintangPassword123!'),
    
    # Staff Divisi Operator (2 akun)
    ('staff_operator1', 'achmadfadhilah63@gmail.com', 'staff', 'Operator', 'BintangPassword123!'),
    ('staff_operator2', 'brandydesign14@gmail.com', 'staff', 'Operator', 'BintangPassword123!'),
    
    # Staff Divisi Finishing (2 akun)
    ('staff_finishing1', 'achmadfadhilah63@gmail.com', 'staff', 'Finishing', 'BintangPassword123!'),
    ('staff_finishing2', 'brandydesign14@gmail.com', 'staff', 'Finishing', 'BintangPassword123!'),
]

print("\n=== MEMPROSES PEMBUATAN AKUN ===")
for username, email, role, div_name, password in accounts:
    div_obj = divisi_objs[div_name] if div_name else None
    
    # Cek apakah user sudah ada
    if not CustomUser.objects.filter(username=username).exists():
        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=role,
            divisi=div_obj,
            is_active=True
        )
        # Jika owner, jadikan superuser / staff admin
        if role == 'owner':
            user.is_superuser = True
            user.is_staff = True
            user.save()
            
        print(f"[SUCCESS] Akun [{role.upper()}] '{username}' ({email}) berhasil dibuat!")
    else:
        # Jika sudah ada, update password dan pastikan aktif
        user = CustomUser.objects.get(username=username)
        user.email = email
        user.set_password(password)
        user.role = role
        user.divisi = div_obj
        user.is_active = True
        if role == 'owner':
            user.is_superuser = True
            user.is_staff = True
        user.save()
        print(f"[INFO] Akun [{role.upper()}] '{username}' sudah ada. Info & password diperbarui!")

print("\nProses inisialisasi 8 akun produksi berhasil selesai dengan sukses!")
