from datetime import timedelta
import os
import secrets
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.contrib.auth.password_validation import validate_password

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
from api.permissions import IsOwnerOrManager, IsStrictOwnerOrManager
from api.throttles import LoginRateThrottle, PasswordResetRequestThrottle, PasswordResetVerifyThrottle

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
    """Ambil IP klien hanya dari rantai proxy yang jumlahnya dikonfigurasi."""
    remote = request.META.get("REMOTE_ADDR")
    num_proxies = int(getattr(settings, "NUM_PROXIES", 0) or 0)
    xff = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
    if num_proxies > 0 and len(xff) >= num_proxies:
        return xff[-num_proxies]
    return remote


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
    throttle_classes = [LoginRateThrottle]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        ip = _get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")
        username_input = request.data.get("username", "")

        failure_key = f"login_fail:{username_input.strip().lower()}"
        if cache.get(f"{failure_key}:locked"):
            return Response({"detail": "Terlalu banyak percobaan. Coba lagi 15 menit."}, status=429)
        try:
            serializer.is_valid(raise_exception=True)
        except (InvalidToken, TokenError) as e:
            attempts = int(cache.get(failure_key, 0) or 0) + 1
            cache.set(failure_key, attempts, 900)
            if attempts >= 5:
                cache.set(f"{failure_key}:locked", True, 900)
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
        cache.delete(failure_key)
        cache.delete(f"{failure_key}:locked")
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

    permission_classes = [IsStrictOwnerOrManager]

    def get(self, request):
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

    permission_classes = [IsStrictOwnerOrManager]

    def get(self, request):
        sessions = SessionToken.objects.filter(is_active=True).select_related("user")
        serializer = SessionTokenSerializer(sessions, many=True)
        return Response(serializer.data)


class SessionRevokeView(APIView):
    """DELETE /api/security/sessions/{id}/ — Paksa logout satu sesi."""

    permission_classes = [IsStrictOwnerOrManager]

    def delete(self, request, pk):
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
    """Selalu memberi respons generik untuk mencegah enumerasi username."""
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRequestThrottle]

    def post(self, request):
        username = str(request.data.get("username") or "").strip()
        if not username:
            return Response({"detail": "Jika akun dan email valid, kode OTP akan dikirim."})
        reset_token = uuid.uuid4().hex
        user = CustomUser.objects.filter(username=username).first()
        if user and user.email:
            otp = str(secrets.SystemRandom().randint(100000, 999999))
            cache.set(f"pw_reset:{reset_token}", {
                "username": user.username, "otp": otp, "attempts": 0,
            }, 300)
            subject = "[Brandy CRM] Kode OTP Lupa Password"
            message = (
                f"Halo {user.username},\n\nKode OTP reset password Anda: {otp}\n"
                "Kode berlaku 5 menit. Abaikan bila Anda tidak meminta reset."
            )
            try:
                send_mail(subject, message, None, [user.email], fail_silently=False)
            except Exception:
                cache.delete(f"pw_reset:{reset_token}")
                return Response({"detail": "Layanan email sedang tidak tersedia. Coba lagi nanti."}, status=503)
            SecurityAuditLog.objects.create(
                user=user, username_input=username, event="PASSWORD_RESET_REQUESTED",
                ip_address=_get_client_ip(request), user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan="Permintaan OTP reset password", berhasil=True,
            )
        # Token acak juga dikembalikan untuk akun tidak valid agar bentuk respons identik.
        return Response({
            "detail": "Jika akun dan email valid, kode OTP akan dikirim.",
            "reset_token": reset_token,
        })


class ForgotPasswordVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetVerifyThrottle]

    def post(self, request):
        username = str(request.data.get("username") or "").strip()
        otp = str(request.data.get("otp") or "").strip()
        new_password = request.data.get("new_password")
        reset_token = str(request.data.get("reset_token") or "").strip()
        if not all((username, otp, new_password, reset_token)):
            return Response({"detail": "Data verifikasi tidak lengkap."}, status=400)
        key = f"pw_reset:{reset_token}"
        state = cache.get(key)
        if not state or state.get("username") != username:
            return Response({"detail": "Kode OTP salah atau kedaluwarsa."}, status=400)
        user = CustomUser.objects.filter(username=username).first()
        if not user:
            cache.delete(key)
            return Response({"detail": "Kode OTP salah atau kedaluwarsa."}, status=400)
        if not secrets.compare_digest(str(state.get("otp", "")), otp):
            state["attempts"] = int(state.get("attempts", 0)) + 1
            if state["attempts"] >= 5:
                cache.delete(key)
            else:
                cache.set(key, state, 300)
            SecurityAuditLog.objects.create(
                user=user, username_input=username, event="PASSWORD_RESET_FAILED",
                ip_address=_get_client_ip(request), user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan="OTP reset salah", berhasil=False,
            )
            return Response({"detail": "Kode OTP salah atau kedaluwarsa."}, status=400)
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            return Response({"detail": ", ".join(exc.messages)}, status=400)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        cache.delete(key)
        SessionToken.objects.filter(user=user, is_active=True).update(is_active=False)
        SecurityAuditLog.objects.create(
            user=user, username_input=username, event="PASSWORD_RESET_SUCCEEDED",
            ip_address=_get_client_ip(request), user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan="Reset password via OTP berhasil", berhasil=True,
        )
        return Response({"detail": "Password berhasil diubah. Silakan login kembali."})
