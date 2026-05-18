from django.contrib import admin

from .models import Absensi, Kontrak, StaffAnnouncement


@admin.register(Absensi)
class AbsensiAdmin(admin.ModelAdmin):
    list_display = ["staff", "tanggal", "jam_masuk", "jam_keluar", "status", "diverifikasi"]
    list_filter = ["status", "diverifikasi", "tanggal"]
    search_fields = ["staff__username"]
    date_hierarchy = "tanggal"
    readonly_fields = ["jam_masuk", "jam_keluar"]


@admin.register(Kontrak)
class KontrakAdmin(admin.ModelAdmin):
    list_display = ["nomor_kontrak", "staff", "tipe", "tanggal_mulai", "tanggal_berakhir", "status", "gaji_pokok"]
    list_filter = ["tipe", "status"]
    search_fields = ["nomor_kontrak", "staff__username"]


@admin.register(StaffAnnouncement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["judul", "target", "divisi", "dibuat_oleh", "aktif_sampai", "dibuat_pada"]
    list_filter = ["target"]
