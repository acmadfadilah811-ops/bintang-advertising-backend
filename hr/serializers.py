from rest_framework import serializers

from .models import Absensi, Kontrak, StaffAnnouncement, Akun, TransaksiBukuBesar


class AbsensiSerializer(serializers.ModelSerializer):
    staff_nama = serializers.CharField(source="staff.username", read_only=True)
    divisi_nama = serializers.CharField(source="staff.divisi.nama", read_only=True, default=None)
    durasi_kerja_jam = serializers.FloatField(read_only=True)
    sudah_clock_out = serializers.BooleanField(read_only=True)
    diverifikasi_oleh_nama = serializers.CharField(
        source="diverifikasi_oleh.username", read_only=True, default=None
    )

    class Meta:
        model = Absensi
        fields = [
            "id",
            "staff",
            "staff_nama",
            "divisi_nama",
            "tanggal",
            "jam_masuk",
            "jam_keluar",
            "status",
            "catatan",
            "durasi_kerja_jam",
            "sudah_clock_out",
            "workspace_unlocked",
            "diverifikasi",
            "diverifikasi_oleh_nama",
        ]
        read_only_fields = ["id", "staff", "tanggal", "diverifikasi", "diverifikasi_oleh"]


class KontrakSerializer(serializers.ModelSerializer):
    staff_nama = serializers.CharField(source="staff.username", read_only=True)
    dibuat_oleh_nama = serializers.CharField(source="dibuat_oleh.username", read_only=True)
    is_aktif = serializers.BooleanField(read_only=True)

    class Meta:
        model = Kontrak
        fields = [
            "id",
            "staff",
            "staff_nama",
            "nomor_kontrak",
            "tipe",
            "tanggal_mulai",
            "tanggal_berakhir",
            "gaji_pokok",
            "status",
            "dokumen",
            "catatan",
            "is_aktif",
            "dibuat_oleh",
            "dibuat_oleh_nama",
            "dibuat_pada",
        ]
        read_only_fields = ["id", "dibuat_oleh", "dibuat_pada"]


class AnnouncementSerializer(serializers.ModelSerializer):
    dibuat_oleh_nama = serializers.CharField(source="dibuat_oleh.username", read_only=True)
    divisi_nama = serializers.CharField(source="divisi.nama", read_only=True, default=None)

    class Meta:
        model = StaffAnnouncement
        fields = [
            "id",
            "judul",
            "isi",
            "target",
            "divisi",
            "divisi_nama",
            "staff_personal",
            "dibuat_oleh_nama",
            "aktif_sampai",
            "dibuat_pada",
        ]
        read_only_fields = ["id", "dibuat_pada"]


class AkunSerializer(serializers.ModelSerializer):
    class Meta:
        model = Akun
        fields = ["id", "kode_akun", "nama_akun", "kategori"]


class TransaksiBukuBesarSerializer(serializers.ModelSerializer):
    akun_nama = serializers.CharField(source="akun.nama_akun", read_only=True)
    akun_kode = serializers.CharField(source="akun.kode_akun", read_only=True)

    class Meta:
        model = TransaksiBukuBesar
        fields = [
            "id",
            "akun",
            "akun_nama",
            "akun_kode",
            "tanggal",
            "no_referensi",
            "keterangan",
            "debit",
            "kredit",
            "waktu_input",
        ]
        read_only_fields = ["id", "waktu_input"]
