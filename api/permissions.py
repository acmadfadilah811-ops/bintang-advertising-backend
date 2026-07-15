from rest_framework.permissions import BasePermission, SAFE_METHODS
from django.utils import timezone
from hr.models import Absensi

class IsOwnerOrManager(BasePermission):
    """
    Hanya Owner, Manager, atau Admin. Role kasir telah dikeluarkan.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']
        )

class IsStrictOwnerOrManager(BasePermission):
    """
    Hanya Owner atau Manager saja (tanpa Admin dan Kasir).
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') in ['owner', 'manager']
        )

# Alias for compatibility
IsOwnerManagerOrAdmin = IsOwnerOrManager

class IsOwnerManagerAdminOrKasir(BasePermission):
    """
    Hanya Owner, Manager, Admin, atau Kasir (Staff dilarang).
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') in ['owner', 'manager', 'admin', 'kasir']
        )

class IsOwnerManagerAdminOrReadOnly(BasePermission):
    """
    Owner, Manager, Admin memiliki akses penuh (write/read).
    Kasir dan Staff hanya memiliki akses membaca (SAFE_METHODS).
    """
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']
        )

class IsClockedIn(BasePermission):
    """
    Memvalidasi status clock-in staff.
    - Owner, Manager, Admin di-bypass (selalu True).
    - Staff harus clock-in hari ini (tanggal hari ini di Absensi, jam_masuk not null).
      Jika sudah check-out (jam_keluar not null), hanya diperbolehkan jika workspace_unlocked = True.
    """
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
            
        # Bypass for management roles
        if user.role in ['owner', 'manager', 'admin']:
            return True
            
        # Only staff (and potentially other roles, but mainly staff) require clock-in check
        today = timezone.localdate()
        absensi = Absensi.objects.filter(staff=user, tanggal=today).first()
        if not absensi:
            return False
            
        if not absensi.jam_masuk:
            return False
            
        # If they clocked out, they cannot access unless the workspace was explicitly unlocked by management
        if absensi.jam_keluar is not None and not absensi.workspace_unlocked:
            return False
            
        return True
