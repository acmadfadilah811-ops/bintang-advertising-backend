"""Cakupan test untuk app users: autentikasi & gating izin endpoint keamanan.

Sebelumnya app ini tidak memiliki test sama sekali. Test berikut memakai
force_authenticate agar fokus pada lapisan izin (permission_classes) tanpa
bergantung pada alur login 2FA.
"""
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model

User = get_user_model()


class UsersAuthPermissionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner_u", password="password123", role="owner")
        self.manager = User.objects.create_user(username="manager_u", password="password123", role="manager")
        self.admin = User.objects.create_user(username="admin_u", password="password123", role="admin")
        self.kasir = User.objects.create_user(username="kasir_u", password="password123", role="kasir")
        self.staff = User.objects.create_user(username="staff_u", password="password123", role="staff")

    def test_me_requires_authentication(self):
        resp = self.client.get("/api/users/me/")
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_me_returns_current_user(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.get("/api/users/me/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get("username"), "staff_u")

    def test_audit_log_strict_owner_manager_only(self):
        # IsStrictOwnerOrManager => hanya owner/manager. admin/kasir/staff ditolak.
        for u in [self.staff, self.kasir, self.admin]:
            self.client.force_authenticate(user=u)
            self.assertEqual(
                self.client.get("/api/security/audit-log/").status_code,
                status.HTTP_403_FORBIDDEN,
                msg=f"{u.role} seharusnya ditolak dari audit-log",
            )
        for u in [self.owner, self.manager]:
            self.client.force_authenticate(user=u)
            self.assertNotIn(
                self.client.get("/api/security/audit-log/").status_code,
                [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
            )

    def test_sessions_list_strict_owner_manager_only(self):
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.get("/api/security/sessions/").status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.owner)
        self.assertNotIn(
            self.client.get("/api/security/sessions/").status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_staff_online_management_only(self):
        # StaffOnlineView pakai IsOwnerOrManager (owner/manager/admin).
        for u in [self.staff, self.kasir]:
            self.client.force_authenticate(user=u)
            self.assertEqual(self.client.get("/api/users/online/").status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        self.assertNotIn(
            self.client.get("/api/users/online/").status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_change_password_requires_authentication(self):
        resp = self.client.post("/api/auth/change-password/", {})
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
