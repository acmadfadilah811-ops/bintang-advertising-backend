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

        # 1. Track request yang terauthentikasi via session
        if hasattr(request, "user") and request.user.is_authenticated:
            self._update_last_seen(request.user)
        else:
            # 2. Fallback untuk JWT Authentication di Django REST Framework
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                try:
                    from rest_framework_simplejwt.tokens import AccessToken
                    token = auth_header.split(" ")[1]
                    access_token = AccessToken(token)
                    user_id = access_token.get("user_id")
                    if user_id:
                        self._update_last_seen_by_id(user_id)
                except Exception:
                    pass

        return response

    @staticmethod
    def _update_last_seen_by_id(user_id):
        from .models import Profile
        now = timezone.now()
        try:
            profile = Profile.objects.get(user_id=user_id)
            if profile.last_seen and (now - profile.last_seen) < timedelta(seconds=COOLDOWN_SECONDS):
                return
            Profile.objects.filter(pk=profile.pk).update(last_seen=now)
        except Profile.DoesNotExist:
            pass

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
