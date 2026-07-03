import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.models import CustomUser  # noqa: E402

username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
email = os.getenv('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
password = os.getenv('DJANGO_SUPERUSER_PASSWORD')

if not password:
    print("[WARNING] DJANGO_SUPERUSER_PASSWORD environment variable not set. Using default 'admin'.")
    password = 'admin'

if not CustomUser.objects.filter(username=username).exists():
    user = CustomUser.objects.create_superuser(
        username=username,
        email=email,
        password=password,
        role='owner'
    )
    print(f"[SUCCESS] Superuser '{username}' berhasil dibuat!")
else:
    user = CustomUser.objects.get(username=username)
    user.set_password(password)
    user.is_superuser = True
    user.is_staff = True
    user.role = 'owner'
    user.save()
    print(f"[INFO] Password untuk superuser '{username}' telah diperbarui!")
