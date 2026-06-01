import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.models import CustomUser  # noqa: E402

username = 'admin'
email = 'admin@example.com'
password = 'admin'

if not CustomUser.objects.filter(username=username).exists():
    user = CustomUser.objects.create_superuser(
        username=username,
        email=email,
        password=password,
        role='owner'
    )
    print(f"✅ Superuser '{username}' dengan password '{password}' berhasil dibuat!")
else:
    user = CustomUser.objects.get(username=username)
    user.set_password(password)
    user.is_superuser = True
    user.is_staff = True
    user.role = 'owner'
    user.save()
    print(f"🔄 Password untuk superuser '{username}' telah diperbarui ke '{password}'!")
