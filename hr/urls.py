from django.urls import path

from .views import (
    AbsensiListView,
    AbsensiVerifikasiView,
    AnnouncementView,
    ClockInView,
    ClockOutView,
    KontrakDetailView,
    KontrakView,
    StaffDashboardView,
    TimecardView,
)

urlpatterns = [
    # Dashboard Staff (satu endpoint ringkasan)
    path("dashboard/staff/", StaffDashboardView.as_view(), name="staff_dashboard"),

    # Absensi
    path("absensi/clock-in/", ClockInView.as_view(), name="clock_in"),
    path("absensi/clock-out/", ClockOutView.as_view(), name="clock_out"),
    path("absensi/", AbsensiListView.as_view(), name="absensi_list"),
    path("absensi/<int:pk>/verifikasi/", AbsensiVerifikasiView.as_view(), name="absensi_verifikasi"),

    # Timecard
    path("timecard/", TimecardView.as_view(), name="timecard"),

    # Kontrak
    path("kontrak/", KontrakView.as_view(), name="kontrak_list"),
    path("kontrak/<int:pk>/", KontrakDetailView.as_view(), name="kontrak_detail"),

    # Info / Pengumuman
    path("info/", AnnouncementView.as_view(), name="announcement"),
]
