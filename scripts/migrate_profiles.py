# ruff: noqa: E402
"""
Script: Auto-create Profile untuk semua CustomUser yang sudah ada di database.
Jalankan SEKALI setelah migrate:
    python scripts/migrate_profiles.py
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from api.models import CustomUser
from users.models import Profile


def run():
    users = CustomUser.objects.all()
    created_count = 0
    already_exist = 0

    for user in users:
        role = getattr(user, "role", "staff")
        profile, created = Profile.objects.get_or_create(
            user=user,
            defaults={
                "is_organization_admin": role == "owner",
                "has_production_access": role in ("manager", "staff"),
                "has_finance_access": role in ("owner", "manager"),
            },
        )
        if created:
            created_count += 1
            print(f"  ✅ Profile dibuat untuk: {user.username} (role: {role})")
        else:
            already_exist += 1
            print(f"  ⏭  Profile sudah ada untuk: {user.username}")

    print(f"\n✔ Selesai! {created_count} Profile baru dibuat, {already_exist} sudah ada.")


if __name__ == "__main__":
    print("🔄 Membuat Profile untuk semua user lama...\n")
    run()
