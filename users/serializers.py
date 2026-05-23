from rest_framework import serializers

from api.models import CustomUser
from .models import Profile, SecurityAuditLog, SessionToken


class ProfileSerializer(serializers.ModelSerializer):
    is_online = serializers.BooleanField(read_only=True)
    role = serializers.CharField(source="user.role", read_only=True)
    divisi_nama = serializers.CharField(
        source="user.divisi.nama", read_only=True, default=None
    )

    class Meta:
        model = Profile
        fields = [
            "id",
            "is_organization_admin",
            "has_production_access",
            "has_finance_access",
            "is_online",
            "last_seen",
            "date_of_joining",
            "role",
            "divisi_nama",
        ]
        read_only_fields = ["id", "last_seen"]


class UserMeSerializer(serializers.ModelSerializer):
    """Serializer lengkap untuk endpoint /me/ — data user + profil terintegrasi."""

    profile = ProfileSerializer(read_only=True)
    divisi_nama = serializers.CharField(source="divisi.nama", read_only=True, default=None)
    is_online = serializers.SerializerMethodField()
    
    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "divisi",
            "divisi_nama",
            "no_hp",
            "kota",
            "negara",
            "alamat",
            "bio",
            "foto_profil",
            "last_login",
            "date_joined",
            "is_online",
            "profile",
            "status_karyawan",
            "jenis_kontrak",
            "kontrak_mulai",
            "kontrak_selesai",
            "no_kpj",
            "bpjs_kes",
            "file_pkwt",
        ]
        read_only_fields = ["id", "role", "last_login", "date_joined"]

    def get_is_online(self, obj):
        if hasattr(obj, 'profile'):
            return obj.profile.is_online
        return False




class StaffStatusSerializer(serializers.ModelSerializer):
    """Ringkas untuk tampilan status staff di Owner Dashboard."""

    is_online = serializers.SerializerMethodField()
    last_seen = serializers.SerializerMethodField()
    divisi_nama = serializers.CharField(source="divisi.nama", read_only=True, default=None)
    absensi_hari_ini = serializers.SerializerMethodField()
    job_aktif = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "first_name",
            "role",
            "divisi_nama",
            "foto_profil",
            "is_online",
            "last_seen",
            "absensi_hari_ini",
            "job_aktif",
        ]

    def get_is_online(self, obj):
        try:
            return obj.profile.is_online
        except Exception:
            return False

    def get_last_seen(self, obj):
        try:
            ls = obj.profile.last_seen
            return ls.strftime("%H:%M") if ls else None
        except Exception:
            return None

    def get_absensi_hari_ini(self, obj):
        from django.utils import timezone
        from hr.models import Absensi

        today = timezone.localdate()
        absensi = Absensi.objects.filter(staff=obj, tanggal=today).first()
        if not absensi:
            return {"status": "belum_absen", "jam_masuk": None, "jam_keluar": None}
        return {
            "status": absensi.status,
            "jam_masuk": absensi.jam_masuk.strftime("%H:%M") if absensi.jam_masuk else None,
            "jam_keluar": absensi.jam_keluar.strftime("%H:%M") if absensi.jam_keluar else None,
        }

    def get_job_aktif(self, obj):
        from api.models import JobBoard

        return JobBoard.objects.filter(
            pic_staff=obj, status_pekerjaan__in=["antrean", "dikerjakan"]
        ).count()


class SessionTokenSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = SessionToken
        fields = [
            "id",
            "username",
            "ip_address",
            "device_name",
            "user_agent",
            "is_active",
            "is_expired",
            "last_used_at",
            "created_at",
            "expires_at",
        ]

    def get_is_expired(self, obj):
        from django.utils import timezone

        return obj.expires_at < timezone.now()


class SecurityAuditLogSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = SecurityAuditLog
        fields = [
            "id",
            "username",
            "event",
            "ip_address",
            "device_name" if hasattr(SecurityAuditLog, "device_name") else "user_agent",
            "keterangan",
            "berhasil",
            "waktu",
        ]

    def get_username(self, obj):
        if obj.user:
            return obj.user.username
        return obj.username_input or "Unknown"


class SecurityAuditLogSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = SecurityAuditLog
        fields = [
            "id",
            "username",
            "event",
            "ip_address",
            "user_agent",
            "keterangan",
            "berhasil",
            "waktu",
        ]

    def get_username(self, obj):
        if obj.user:
            return obj.user.username
        return obj.username_input or "Unknown"
