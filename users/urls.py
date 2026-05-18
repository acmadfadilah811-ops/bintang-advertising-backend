from django.urls import path

from .views import (
    AuditLogView,
    CustomLoginView,
    LogoutView,
    MeView,
    SessionListView,
    SessionRevokeView,
    StaffOnlineView,
)

urlpatterns = [
    # Auth
    path("auth/login/", CustomLoginView.as_view(), name="custom_login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),

    # Profile
    path("users/me/", MeView.as_view(), name="user_me"),
    path("users/online/", StaffOnlineView.as_view(), name="staff_online"),

    # Security (Owner only)
    path("security/audit-log/", AuditLogView.as_view(), name="audit_log"),
    path("security/sessions/", SessionListView.as_view(), name="session_list"),
    path("security/sessions/<int:pk>/", SessionRevokeView.as_view(), name="session_revoke"),
]
