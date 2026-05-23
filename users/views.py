from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken
from rest_framework_simplejwt.views import TokenObtainPairView

from api.models import CustomUser
from api.views import IsOwnerOrManager

from .models import Profile, SecurityAuditLog, SessionToken
from .serializers import (
    SecurityAuditLogSerializer,
    SessionTokenSerializer,
    StaffStatusSerializer,
    UserMeSerializer,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _parse_device(user_agent: str) -> str:
    """Buat label device singkat dari User-Agent string."""
    ua = user_agent.lower()
    browser = "Browser"
    if "chrome" in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    elif "edge" in ua:
        browser = "Edge"

    os_name = "Unknown OS"
    if "windows" in ua:
        os_name = "Windows"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "linux" in ua:
        os_name = "Linux"
    elif "mac" in ua:
        os_name = "macOS"

    return f"{browser} di {os_name}"


# ---------------------------------------------------------------------------
# Custom Login View — extend TokenObtainPairView
# ---------------------------------------------------------------------------

class CustomLoginView(TokenObtainPairView):
    """
    POST /api/auth/login/
    Sama seperti JWT login biasa, tapi:
    - Catat SecurityAuditLog (berhasil/gagal)
    - Buat SessionToken baru
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        ip = _get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")
        username_input = request.data.get("username", "")

        try:
            serializer.is_valid(raise_exception=True)
        except (InvalidToken, TokenError) as e:
            # --- Login GAGAL ---
            SecurityAuditLog.objects.create(
                user=None,
                username_input=username_input,
                event="LOGIN_FAILED",
                ip_address=ip,
                user_agent=ua,
                keterangan=str(e),
                berhasil=False,
            )
            return Response(
                {"detail": "Username atau password salah."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # --- Login BERHASIL ---
        user = serializer.user
        tokens = serializer.validated_data
        access_token = tokens["access"]
        refresh_token = tokens["refresh"]

        import uuid
        # Decode JTI dari access token
        try:
            decoded = UntypedToken(access_token)
            jti = decoded.get("jti") or uuid.uuid4().hex
            exp = decoded.get("exp", 0)
            expires_at = timezone.datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:
            jti = uuid.uuid4().hex
            expires_at = timezone.now() + timedelta(days=7)

        # Simpan SessionToken
        SessionToken.objects.create(
            user=user,
            token_jti=jti,
            ip_address=ip,
            user_agent=ua,
            device_name=_parse_device(ua),
            expires_at=expires_at,
        )

        # Catat audit log
        SecurityAuditLog.objects.create(
            user=user,
            username_input=user.username,
            event="LOGIN_SUCCESS",
            ip_address=ip,
            user_agent=ua,
            keterangan=f"Login dari {_parse_device(ua)}",
            berhasil=True,
        )

        # Update last_seen langsung
        Profile.objects.filter(user=user).update(last_seen=timezone.now())
        
        user_data = UserMeSerializer(user, context={"request": request}).data

        return Response(
            {
                "access": str(access_token),
                "refresh": str(refresh_token),
                "user": user_data,
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Logout View
# ---------------------------------------------------------------------------

class LogoutView(APIView):
    """
    POST /api/auth/logout/
    Body: { "refresh": "<refresh_token>" }
    Revoke session token + catat audit log.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_raw = request.data.get("refresh", "")
        ip = _get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")

        try:
            token = RefreshToken(refresh_raw)
            jti = token.get("jti", "")
            # Blacklist jika tersedia
            token.blacklist()
        except Exception:
            jti = ""

        # Revoke SessionToken di DB kita
        if jti:
            session = SessionToken.objects.filter(token_jti=jti, user=request.user).first()
            if session:
                session.revoke()

        SecurityAuditLog.objects.create(
            user=request.user,
            username_input=request.user.username,
            event="LOGOUT",
            ip_address=ip,
            user_agent=ua,
            berhasil=True,
        )

        # Force offline status
        try:
            profile = request.user.profile
            profile.last_seen = None
            profile.save(update_fields=['last_seen'])
        except Exception:
            pass

        return Response({"detail": "Logout berhasil."}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Profile: Me (lihat & update profil sendiri)
# ---------------------------------------------------------------------------

class MeView(APIView):
    """GET /api/users/me/ — PATCH /api/users/me/"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserMeSerializer(request.user, context={"request": request})
        return Response(serializer.data)

    def patch(self, request):
        # Staff tidak boleh ubah role/divisi diri sendiri
        protected = ["role", "divisi", "is_staff", "is_superuser"]
        data = {k: v for k, v in request.data.items() if k not in protected}
        serializer = UserMeSerializer(
            request.user, data=data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Staff Online — untuk Owner Dashboard
# ---------------------------------------------------------------------------

class StaffOnlineView(APIView):
    """
    GET /api/users/online/
    Mengembalikan daftar semua staff dengan status online/offline.
    Hanya untuk Owner & Manager.
    """

    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        staff_qs = CustomUser.objects.filter(is_active=True).select_related(
            "profile", "divisi"
        )

        # Filter opsional per divisi
        divisi_id = request.query_params.get("divisi")
        if divisi_id:
            staff_qs = staff_qs.filter(divisi_id=divisi_id)

        online_threshold = timezone.now() - timedelta(minutes=15)
        total_online = staff_qs.filter(
            profile__last_seen__gte=online_threshold
        ).count()

        data = StaffStatusSerializer(
            staff_qs, many=True, context={"request": request}
        ).data

        return Response(
            {
                "total_staff": staff_qs.count(),
                "total_online": total_online,
                "staff": data,
            }
        )


# ---------------------------------------------------------------------------
# Security Audit Log — Owner only
# ---------------------------------------------------------------------------

class AuditLogView(APIView):
    """GET /api/security/audit-log/ — Riwayat event keamanan."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != "owner":
            SecurityAuditLog.objects.create(
                user=request.user,
                event="PERMISSION_DENIED",
                ip_address=_get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan="Akses audit log ditolak",
                berhasil=False,
            )
            return Response(
                {"detail": "Hanya Owner yang bisa mengakses audit log."},
                status=status.HTTP_403_FORBIDDEN,
            )

        logs = SecurityAuditLog.objects.all()

        # Filter opsional
        event = request.query_params.get("event")
        if event:
            logs = logs.filter(event=event)
        berhasil = request.query_params.get("berhasil")
        if berhasil is not None:
            logs = logs.filter(berhasil=berhasil.lower() == "true")

        logs = logs[:200]  # Batasi 200 record terbaru
        serializer = SecurityAuditLogSerializer(logs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Session Management — lihat & revoke sesi aktif
# ---------------------------------------------------------------------------

class SessionListView(APIView):
    """GET /api/security/sessions/ — Semua sesi JWT aktif (Owner only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != "owner":
            return Response(
                {"detail": "Hanya Owner yang bisa melihat semua sesi."},
                status=status.HTTP_403_FORBIDDEN,
            )

        sessions = SessionToken.objects.filter(is_active=True).select_related("user")
        serializer = SessionTokenSerializer(sessions, many=True)
        return Response(serializer.data)


class SessionRevokeView(APIView):
    """DELETE /api/security/sessions/{id}/ — Paksa logout satu sesi."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if request.user.role != "owner":
            return Response(
                {"detail": "Hanya Owner yang bisa mencabut sesi."},
                status=status.HTTP_403_FORBIDDEN,
            )

        session = SessionToken.objects.filter(pk=pk, is_active=True).first()
        if not session:
            return Response(
                {"detail": "Sesi tidak ditemukan atau sudah tidak aktif."},
                status=status.HTTP_404_NOT_FOUND,
            )

        session.revoke()
        SecurityAuditLog.objects.create(
            user=request.user,
            event="TOKEN_REVOKED",
            ip_address=_get_client_ip(request),
            keterangan=f"Owner mencabut sesi milik {session.user.username}",
            berhasil=True,
        )

        return Response(
            {
                "detail": f"Sesi milik {session.user.username} berhasil dicabut.",
                "session_id": pk,
            }
        )


# ---------------------------------------------------------------------------
# Permission Denied Tracker — dipanggil oleh views lain
# ---------------------------------------------------------------------------

def log_permission_denied(request, keterangan=""):
    """Helper: catat event PERMISSION_DENIED ke audit log."""
    SecurityAuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        event="PERMISSION_DENIED",
        ip_address=_get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        keterangan=keterangan,
        berhasil=False,
    )
