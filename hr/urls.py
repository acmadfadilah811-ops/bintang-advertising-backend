from django.urls import path

from .views import (
    AbsensiListView,
    AbsensiDetailView,
    AbsensiVerifikasiView,
    AnnouncementView,
    AnnouncementDetailView,
    ClockInView,
    ClockOutView,
    KontrakDetailView,
    KontrakView,
    StaffDashboardView,
    TimecardView,
    AttendanceSessionManagerView,
    UnlockRequestStaffView,
    UnlockRequestManagerView,
    UnlockRequestActionView,
)

urlpatterns = [
    # Dashboard Staff (satu endpoint ringkasan)
    path("dashboard/staff/", StaffDashboardView.as_view(), name="staff_dashboard"),

    # Absensi & Sesi Absensi
    path("attendance-session/", AttendanceSessionManagerView.as_view(), name="attendance_session"),
    path("absensi/clock-in/", ClockInView.as_view(), name="clock_in"),
    path("absensi/clock-out/", ClockOutView.as_view(), name="clock_out"),
    path("absensi/", AbsensiListView.as_view(), name="absensi_list"),
    path("absensi/<int:pk>/", AbsensiDetailView.as_view(), name="absensi_detail"),
    path("absensi/<int:pk>/verifikasi/", AbsensiVerifikasiView.as_view(), name="absensi_verifikasi"),
    
    # Izin Buka Akun (Unlock Request)
    path("unlock-request/", UnlockRequestStaffView.as_view(), name="unlock_request_staff"),
    path("unlock-requests/", UnlockRequestManagerView.as_view(), name="unlock_requests_manager"),
    path("unlock-requests/<int:pk>/<str:action>/", UnlockRequestActionView.as_view(), name="unlock_request_action"),

    # Timecard
    path("timecard/", TimecardView.as_view(), name="timecard"),

    # Kontrak
    path("kontrak/", KontrakView.as_view(), name="kontrak_list"),
    path("kontrak/<int:pk>/", KontrakDetailView.as_view(), name="kontrak_detail"),

    # Info / Pengumuman
    path("info/", AnnouncementView.as_view(), name="announcement"),
    path("info/<int:pk>/", AnnouncementDetailView.as_view(), name="announcement_detail"),
]
