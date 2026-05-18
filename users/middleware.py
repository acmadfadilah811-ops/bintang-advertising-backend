from datetime import timedelta

from django.utils import timezone

# Cooldown: hanya update DB max 1x per 3 menit per user
COOLDOWN_SECONDS = 180


class ActivityTrackingMiddleware:
    """
    Middleware ringan untuk tracking last_seen user.
    Dijalankan setelah setiap request dari user yang sudah login.
    Menggunakan cooldown untuk menghindari hit DB berlebihan.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Hanya track request yang terauthentikasi
        if hasattr(request, "user") and request.user.is_authenticated:
            self._update_last_seen(request.user)

        return response

    @staticmethod
    def _update_last_seen(user):
        from .models import Profile

        now = timezone.now()
        try:
            profile = user.profile
            # Jangan hit DB jika update terakhir masih dalam cooldown window
            if profile.last_seen and (now - profile.last_seen) < timedelta(
                seconds=COOLDOWN_SECONDS
            ):
                return
            Profile.objects.filter(pk=profile.pk).update(last_seen=now)
            # Update cache in-memory agar request ini tidak perlu hit DB lagi
            profile.last_seen = now
        except Profile.DoesNotExist:
            # Fallback: auto-create profile jika belum ada
            Profile.objects.create(user=user, last_seen=now)
