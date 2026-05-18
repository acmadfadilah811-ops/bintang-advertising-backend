from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def auto_create_profile(sender, instance, created, **kwargs):
    """
    Auto-buat Profile setiap kali CustomUser baru dibuat.
    Permission default disesuaikan berdasarkan role.
    """
    if not created:
        return

    role = getattr(instance, "role", "staff")

    Profile.objects.get_or_create(
        user=instance,
        defaults={
            "is_organization_admin": role == "owner",
            "has_production_access": role in ("manager", "staff"),
            "has_finance_access": role in ("owner", "manager"),
        },
    )
