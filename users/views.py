from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken
from rest_framework_simplejwt.views import TokenObtainPairView

from api.models import CustomUser
from api.permissions import IsOwnerOrManager

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
    - Deteksi IP baru & verifikasi keamanan OTP jika IP berbeda
    - Catat SecurityAuditLog
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

        # --- Login BERHASIL (Kredensial Valid) ---
        user = serializer.user

        # Deteksi Perubahan IP jika User memiliki Email terdaftar (bisa dibypass via env)
        requires_verification = False
        import os
        bypass_verification = os.getenv("SECURITY_BYPASS_IP_VERIFICATION", "False").lower() == "true"
        
        if user.email and not bypass_verification:
            last_success = SecurityAuditLog.objects.filter(
                user=user, event="LOGIN_SUCCESS", berhasil=True
            ).order_by("-waktu").first()
            if last_success and last_success.ip_address != ip:
                requires_verification = True

        if requires_verification:
            import secrets
            import uuid
            from django.core.cache import cache
            from django.core.mail import send_mail

            otp = str(secrets.SystemRandom().randint(100000, 999999))
            temp_token = uuid.uuid4().hex

            # Simpan ke cache selama 5 menit
            cache.set(
                f"login_otp_{temp_token}",
                {
                    "user_id": user.id,
                    "otp": otp,
                    "ip": ip,
                    "ua": ua,
                },
                300,
            )

            # Kirim OTP via email
            subject = "[Brandy CRM] Kode Verifikasi Keamanan Login"
            message = f"""Halo {user.username},

Sistem kami mendeteksi upaya login dari alamat IP yang berbeda ({ip}) dibandingkan dengan sesi Anda sebelumnya.

Silakan gunakan kode OTP berikut untuk memverifikasi identitas Anda:
KODE VERIFIKASI: {otp}

Kode ini hanya berlaku selama 5 menit. Jika ini bukan Anda, segera hubungi Owner atau ganti password Anda.

Terima kasih,
Tim Keamanan Brandy CRM
"""
            send_mail(
                subject,
                message,
                None,  # Menggunakan DEFAULT_FROM_EMAIL dari settings
                [user.email],
                fail_silently=True,
            )

            # Catat log percobaan verifikasi keamanan
            SecurityAuditLog.objects.create(
                user=user,
                username_input=user.username,
                event="LOGIN_FAILED",
                ip_address=ip,
                user_agent=ua,
                keterangan=f"Deteksi IP berbeda ({ip}). Meminta verifikasi OTP ke {user.email}",
                berhasil=False,
            )

            # Sembunyikan sebagian email untuk privasi di UI
            email_parts = user.email.split("@")
            masked_email = f"{email_parts[0][:3]}***@{email_parts[1]}" if len(email_parts) == 2 else user.email

            return Response(
                {
                    "detail": "VERIFICATION_REQUIRED",
                    "temp_token": temp_token,
                    "email_masked": masked_email,
                },
                status=status.HTTP_200_OK,
            )

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


class VerifyLoginView(APIView):
    """
    POST /api/auth/verify-login/
    Body: { "temp_token": "...", "otp": "..." }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        temp_token = request.data.get("temp_token")
        otp = request.data.get("otp")

        if not temp_token or not otp:
            return Response(
                {"detail": "Token dan OTP wajib diisi."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.core.cache import cache
        cached_data = cache.get(f"login_otp_{temp_token}")

        if not cached_data or cached_data["otp"] != str(otp):
            # Catat log kegagalan OTP
            SecurityAuditLog.objects.create(
                user=None,
                username_input=f"OTP gagal (temp_token: {temp_token})",
                event="LOGIN_FAILED",
                ip_address=_get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan="Kode OTP salah atau kedaluwarsa",
                berhasil=False,
            )
            return Response(
                {"detail": "Kode verifikasi salah atau kedaluwarsa."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = CustomUser.objects.filter(id=cached_data["user_id"]).first()
        if not user:
            return Response(
                {"detail": "User tidak ditemukan."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate JWT Token sukses
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        import uuid
        # Simpan SessionToken
        try:
            decoded = UntypedToken(access_token)
            jti = decoded.get("jti") or uuid.uuid4().hex
            exp = decoded.get("exp", 0)
            expires_at = timezone.datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:
            jti = uuid.uuid4().hex
            expires_at = timezone.now() + timedelta(days=7)

        SessionToken.objects.create(
            user=user,
            token_jti=jti,
            ip_address=cached_data["ip"],
            user_agent=cached_data["ua"],
            device_name=_parse_device(cached_data["ua"]),
            expires_at=expires_at,
        )

        # Catat audit log sukses
        SecurityAuditLog.objects.create(
            user=user,
            username_input=user.username,
            event="LOGIN_SUCCESS",
            ip_address=cached_data["ip"],
            user_agent=cached_data["ua"],
            keterangan=f"Login terverifikasi OTP dari {_parse_device(cached_data['ua'])}",
            berhasil=True,
        )

        Profile.objects.filter(user=user).update(last_seen=timezone.now())
        user_data = UserMeSerializer(user, context={"request": request}).data

        # Hapus OTP dari cache
        cache.delete(f"login_otp_{temp_token}")

        return Response(
            {
                "access": access_token,
                "refresh": refresh_token,
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
        staff_qs = CustomUser.objects.filter(is_active=True, role="staff").select_related(
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


class ChangePasswordView(APIView):
    """
    POST /api/auth/change-password/
    Body: { "old_password": "...", "new_password": "..." }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")

        if not old_password or not new_password:
            return Response(
                {"detail": "Password lama dan baru wajib diisi."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not user.check_password(old_password):
            return Response(
                {"detail": "Password lama salah."},
                status=status.HTTP_400_BAD_REQUEST
            )

        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response(
                {"detail": ", ".join(e.messages)},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()

        # Catat audit log
        SecurityAuditLog.objects.create(
            user=user,
            username_input=user.username,
            event="PASSWORD_CHANGED",
            ip_address=_get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan="User mengganti password mandiri",
            berhasil=True,
        )

        return Response({"detail": "Password berhasil diubah."}, status=status.HTTP_200_OK)


class ForgotPasswordRequestView(APIView):
    """
    POST /api/auth/forgot-password/request/
    Body: { "username": "..." }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username")
        if not username:
            return Response({"detail": "Username wajib diisi."}, status=status.HTTP_400_BAD_REQUEST)

        user = CustomUser.objects.filter(username=username).first()
        if not user:
            return Response({"detail": "Username tidak terdaftar."}, status=status.HTTP_404_NOT_FOUND)

        if not user.email:
            return Response(
                {"detail": "Akun Anda belum memiliki email pemulihan terdaftar. Silakan hubungi Owner atau Manager untuk mereset kata sandi Anda."},
                status=status.HTTP_400_BAD_REQUEST
            )

        import secrets
        from django.core.cache import cache
        from django.core.mail import send_mail

        otp = str(secrets.SystemRandom().randint(100000, 999999))
        cache.set(f"pw_reset_otp_{username}", otp, 300) # Berlaku 5 menit

        # Kirim email
        subject = "[Brandy CRM] Kode OTP Lupa Password"
        message = f"""Halo {user.username},

Anda menerima email ini karena ada permintaan untuk mengatur ulang kata sandi Akun Brandy CRM Anda.

Silakan gunakan kode OTP berikut untuk mereset kata sandi Anda:
KODE OTP: {otp}

Kode ini hanya berlaku selama 5 menit. Jika Anda tidak meminta ini, abaikan email ini secara aman.

Terima kasih,
Tim Keamanan Brandy CRM
"""
        send_mail(
            subject,
            message,
            None,
            [user.email],
            fail_silently=True
        )

        # Sembunyikan sebagian email untuk privasi
        email_parts = user.email.split("@")
        masked_email = f"{email_parts[0][:3]}***@{email_parts[1]}" if len(email_parts) == 2 else user.email

        # Catat audit log percobaan
        SecurityAuditLog.objects.create(
            user=user,
            username_input=username,
            event="PASSWORD_CHANGED",
            ip_address=_get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan=f"Meminta OTP reset password ke {user.email}",
            berhasil=False,
        )

        return Response({
            "detail": "OTP_SENT",
            "email_masked": masked_email
        }, status=status.HTTP_200_OK)


class ForgotPasswordVerifyView(APIView):
    """
    POST /api/auth/forgot-password/verify/
    Body: { "username": "...", "otp": "...", "new_password": "..." }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username")
        otp = request.data.get("otp")
        new_password = request.data.get("new_password")

        if not username or not otp or not new_password:
            return Response({"detail": "Username, OTP, dan Password Baru wajib diisi."}, status=status.HTTP_400_BAD_REQUEST)

        user = CustomUser.objects.filter(username=username).first()
        if not user:
            return Response({"detail": "User tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)

        from django.core.cache import cache
        cached_otp = cache.get(f"pw_reset_otp_{username}")

        if not cached_otp or cached_otp != str(otp):
            # Catat log audit kegagalan
            SecurityAuditLog.objects.create(
                user=user,
                username_input=username,
                event="PASSWORD_CHANGED",
                ip_address=_get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan="Gagal mereset password: Kode OTP salah atau kedaluwarsa",
                berhasil=False,
            )
            return Response({"detail": "Kode OTP salah atau kedaluwarsa."}, status=status.HTTP_400_BAD_REQUEST)

        # Validasi kekuatan password
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response({"detail": ", ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)

        # Update password
        user.set_password(new_password)
        user.save()

        # Bersihkan OTP dari cache
        cache.delete(f"pw_reset_otp_{username}")

        # Catat audit log sukses
        SecurityAuditLog.objects.create(
            user=user,
            username_input=username,
            event="PASSWORD_CHANGED",
            ip_address=_get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan="Reset password mandiri via OTP Lupa Password sukses",
            berhasil=True,
        )

        return Response({"detail": "Password berhasil diubah. Silakan login kembali."}, status=status.HTTP_200_OK)


