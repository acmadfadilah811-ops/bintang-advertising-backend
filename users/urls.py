from django.urls import path

from .views import (
    AuditLogView,
    CustomLoginView,
    LogoutView,
    MeView,
    SessionListView,
    SessionRevokeView,
    StaffOnlineView,
    ChangePasswordView,
    VerifyLoginView,
    ForgotPasswordRequestView,
    ForgotPasswordVerifyView,
)

urlpatterns = [
    # Auth
    path("auth/login/", CustomLoginView.as_view(), name="custom_login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change_password"),
    path("auth/verify-login/", VerifyLoginView.as_view(), name="verify_login"),
    path("auth/forgot-password/request/", ForgotPasswordRequestView.as_view(), name="forgot_password_request"),
    path("auth/forgot-password/verify/", ForgotPasswordVerifyView.as_view(), name="forgot_password_verify"),

    # Profile
    path("users/me/", MeView.as_view(), name="user_me"),
    path("users/online/", StaffOnlineView.as_view(), name="staff_online"),

    # Security (Owner only)
    path("security/audit-log/", AuditLogView.as_view(), name="audit_log"),
    path("security/sessions/", SessionListView.as_view(), name="session_list"),
    path("security/sessions/<int:pk>/", SessionRevokeView.as_view(), name="session_revoke"),
]


