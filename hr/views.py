from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import JobBoard
from api.views import IsOwnerOrManager

from .models import Absensi, Kontrak, StaffAnnouncement
from .serializers import AbsensiSerializer, AnnouncementSerializer, KontrakSerializer


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

class IsOwnerOrManagerPerm(IsOwnerOrManager):
    """Alias agar tidak konflik nama."""
    pass


# ---------------------------------------------------------------------------
# 1. ABSENSI — Clock-in / Clock-out
# ---------------------------------------------------------------------------

class ClockInView(APIView):
    """
    POST /api/hr/absensi/clock-in/
    Staff menekan tombol MASUK. Membuat record Absensi untuk hari ini.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        today = timezone.localdate()
        now = timezone.now()

        existing = Absensi.objects.filter(staff=request.user, tanggal=today).first()
        if existing:
            return Response(
                {
                    "detail": "Anda sudah clock-in hari ini.",
                    "jam_masuk": existing.jam_masuk,
                    "sudah_clock_out": existing.sudah_clock_out,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        absensi = Absensi.objects.create(
            staff=request.user,
            tanggal=today,
            jam_masuk=now,
            status="hadir",
            catatan=request.data.get("catatan", ""),
        )
        return Response(
            {
                "detail": f"Clock-in berhasil pukul {now.strftime('%H:%M')}.",
                "absensi": AbsensiSerializer(absensi).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ClockOutView(APIView):
    """
    POST /api/hr/absensi/clock-out/
    Staff menekan tombol KELUAR. Mengisi jam_keluar pada record hari ini.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        today = timezone.localdate()
        now = timezone.now()

        absensi = Absensi.objects.filter(
            staff=request.user, tanggal=today, jam_keluar__isnull=True
        ).first()

        if not absensi:
            return Response(
                {"detail": "Belum ada clock-in hari ini, atau sudah clock-out."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        absensi.jam_keluar = now
        if request.data.get("catatan"):
            absensi.catatan = (absensi.catatan + " | " + request.data["catatan"]).strip(" |")
        absensi.save(update_fields=["jam_keluar", "catatan"])

        return Response(
            {
                "detail": f"Clock-out berhasil pukul {now.strftime('%H:%M')}.",
                "durasi_kerja_jam": absensi.durasi_kerja_jam,
                "absensi": AbsensiSerializer(absensi).data,
            }
        )


class AbsensiListView(APIView):
    """
    GET /api/hr/absensi/
    Staff: lihat absensi milik sendiri
    Manager/Owner: lihat semua (filter by ?staff_id=, ?bulan=, ?tahun=)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        bulan = request.query_params.get("bulan", timezone.localdate().month)
        tahun = request.query_params.get("tahun", timezone.localdate().year)

        if user.role in ("owner", "manager"):
            qs = Absensi.objects.filter(
                tanggal__month=bulan, tanggal__year=tahun
            ).select_related("staff", "staff__divisi")
            staff_id = request.query_params.get("staff_id")
            if staff_id:
                qs = qs.filter(staff_id=staff_id)
        else:
            qs = Absensi.objects.filter(
                staff=user, tanggal__month=bulan, tanggal__year=tahun
            )

        serializer = AbsensiSerializer(qs, many=True)
        return Response(serializer.data)


class AbsensiVerifikasiView(APIView):
    """
    PATCH /api/hr/absensi/{id}/verifikasi/
    Manager/Owner menandai absensi sudah diverifikasi.
    """
    permission_classes = [IsOwnerOrManagerPerm]

    def patch(self, request, pk):
        absensi = Absensi.objects.filter(pk=pk).first()
        if not absensi:
            return Response({"detail": "Absensi tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)

        absensi.diverifikasi = True
        absensi.diverifikasi_oleh = request.user
        absensi.save(update_fields=["diverifikasi", "diverifikasi_oleh"])
        return Response({"detail": "Absensi berhasil diverifikasi.", "id": pk})


# ---------------------------------------------------------------------------
# 2. TIMECARD — Rekap jam kerja + insentif (agregasi dari Absensi + JobBoard)
# ---------------------------------------------------------------------------

class TimecardView(APIView):
    """
    GET /api/hr/timecard/
    Staff: timecard sendiri. Manager/Owner: semua atau filter ?staff_id=
    Query params: ?bulan=5&tahun=2026
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from api.models import CustomUser

        bulan = int(request.query_params.get("bulan", timezone.localdate().month))
        tahun = int(request.query_params.get("tahun", timezone.localdate().year))
        user = request.user

        # Tentukan target staff
        if user.role in ("owner", "manager"):
            staff_id = request.query_params.get("staff_id")
            if staff_id:
                target_users = CustomUser.objects.filter(pk=staff_id)
            else:
                target_users = CustomUser.objects.filter(is_active=True).exclude(role="owner")
        else:
            target_users = CustomUser.objects.filter(pk=user.pk)

        result = []
        for staff in target_users.select_related("divisi"):
            absensi_qs = Absensi.objects.filter(
                staff=staff, tanggal__month=bulan, tanggal__year=tahun
            )

            total_hadir = absensi_qs.filter(status="hadir").count()
            total_wfh = absensi_qs.filter(status="wfh").count()
            total_izin = absensi_qs.filter(status="izin").count()
            total_sakit = absensi_qs.filter(status="sakit").count()
            total_alpha = absensi_qs.filter(status="alpha").count()

            # Hitung total jam kerja
            total_jam = sum(a.durasi_kerja_jam for a in absensi_qs)

            # Total insentif dari JobBoard bulan ini
            total_insentif = (
                JobBoard.objects.filter(
                    pic_staff=staff,
                    status_pekerjaan="selesai",
                    waktu_selesai__month=bulan,
                    waktu_selesai__year=tahun,
                ).aggregate(total=Sum("insentif"))["total"]
                or 0
            )

            # Job selesai bulan ini
            job_selesai = JobBoard.objects.filter(
                pic_staff=staff,
                status_pekerjaan="selesai",
                waktu_selesai__month=bulan,
                waktu_selesai__year=tahun,
            ).count()

            result.append(
                {
                    "staff_id": staff.id,
                    "staff_nama": staff.get_full_name() or staff.username,
                    "username": staff.username,
                    "divisi": staff.divisi.nama if staff.divisi else None,
                    "bulan": bulan,
                    "tahun": tahun,
                    "total_hadir": total_hadir,
                    "total_wfh": total_wfh,
                    "total_izin": total_izin,
                    "total_sakit": total_sakit,
                    "total_alpha": total_alpha,
                    "total_jam_kerja": round(total_jam, 2),
                    "job_selesai": job_selesai,
                    "total_insentif": total_insentif,
                    "riwayat_absensi": AbsensiSerializer(absensi_qs, many=True).data,
                }
            )

        return Response(result)


# ---------------------------------------------------------------------------
# 3. KONTRAK
# ---------------------------------------------------------------------------

class KontrakView(APIView):
    """
    GET  /api/hr/kontrak/       → Owner/Manager: semua | Staff: milik sendiri
    POST /api/hr/kontrak/       → Owner & Manager: buat kontrak baru
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role in ("owner", "manager"):
            qs = Kontrak.objects.all().select_related("staff", "dibuat_oleh")
            staff_id = request.query_params.get("staff_id")
            if staff_id:
                qs = qs.filter(staff_id=staff_id)
            status_filter = request.query_params.get("status")
            if status_filter:
                qs = qs.filter(status=status_filter)
        else:
            qs = Kontrak.objects.filter(staff=user).select_related("dibuat_oleh")

        return Response(KontrakSerializer(qs, many=True).data)

    def post(self, request):
        if request.user.role not in ("owner", "manager"):
            return Response(
                {"detail": "Hanya Owner dan Manager yang bisa membuat kontrak."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = KontrakSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(dibuat_oleh=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class KontrakDetailView(APIView):
    """PATCH /api/hr/kontrak/{id}/ — Update status atau data kontrak."""
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        kontrak = Kontrak.objects.filter(pk=pk).first()
        if not kontrak:
            return None, Response({"detail": "Kontrak tidak ditemukan."}, status=404)
        if user.role not in ("owner", "manager") and kontrak.staff != user:
            return None, Response({"detail": "Akses ditolak."}, status=403)
        return kontrak, None

    def get(self, request, pk):
        kontrak, err = self.get_object(pk, request.user)
        if err:
            return err
        return Response(KontrakSerializer(kontrak).data)

    def patch(self, request, pk):
        if request.user.role not in ("owner", "manager"):
            return Response({"detail": "Akses ditolak."}, status=status.HTTP_403_FORBIDDEN)
        kontrak, err = self.get_object(pk, request.user)
        if err:
            return err
        serializer = KontrakSerializer(kontrak, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 4. PENGUMUMAN / INFO
# ---------------------------------------------------------------------------

class AnnouncementView(APIView):
    """
    GET  /api/hr/info/ → Staff: pengumuman yang relevan untuk mereka
    POST /api/hr/info/ → Owner/Manager: buat pengumuman baru
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Q
        from django.utils import timezone

        user = request.user
        today = timezone.localdate()

        # Filter pengumuman yang masih aktif
        base_qs = StaffAnnouncement.objects.filter(
            Q(aktif_sampai__isnull=True) | Q(aktif_sampai__gte=today)
        ).select_related("divisi", "dibuat_oleh")

        if user.role in ("owner", "manager"):
            qs = base_qs
        else:
            # Staff hanya lihat: semua staff, divisinya, atau personal untuknya
            qs = base_qs.filter(
                Q(target="semua")
                | Q(target="divisi", divisi=user.divisi)
                | Q(target="personal", staff_personal=user)
            )

        return Response(AnnouncementSerializer(qs, many=True).data)

    def post(self, request):
        if request.user.role not in ("owner", "manager"):
            return Response(
                {"detail": "Hanya Owner dan Manager yang bisa membuat pengumuman."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(dibuat_oleh=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 5. STAFF DASHBOARD — Satu endpoint ringkasan untuk staff
# ---------------------------------------------------------------------------

class StaffDashboardView(APIView):
    """
    GET /api/hr/dashboard/staff/
    Mengembalikan semua data yang dibutuhkan halaman dashboard staff:
    - Status absensi hari ini
    - Timecard bulan ini (ringkasan)
    - Kontrak aktif
    - Pengumuman terbaru
    - Job aktif
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        today = timezone.localdate()
        now_month = today.month
        now_year = today.year

        # --- Absensi Hari Ini ---
        absensi_hari_ini = Absensi.objects.filter(staff=user, tanggal=today).first()
        absensi_data = (
            AbsensiSerializer(absensi_hari_ini).data
            if absensi_hari_ini
            else {"status": "belum_absen", "jam_masuk": None, "jam_keluar": None}
        )

        # --- Timecard Ringkasan Bulan Ini ---
        absensi_bulan = Absensi.objects.filter(
            staff=user, tanggal__month=now_month, tanggal__year=now_year
        )
        total_hadir = absensi_bulan.filter(status__in=["hadir", "wfh"]).count()
        total_jam = sum(a.durasi_kerja_jam for a in absensi_bulan)
        total_insentif = (
            JobBoard.objects.filter(
                pic_staff=user,
                status_pekerjaan="selesai",
                waktu_selesai__month=now_month,
                waktu_selesai__year=now_year,
            ).aggregate(total=Sum("insentif"))["total"]
            or 0
        )

        # --- Kontrak Aktif ---
        kontrak = Kontrak.objects.filter(staff=user, status="aktif").first()
        kontrak_data = KontrakSerializer(kontrak).data if kontrak else None

        # --- Pengumuman Terbaru (5 terbaru) ---
        from django.db.models import Q

        pengumuman = StaffAnnouncement.objects.filter(
            Q(aktif_sampai__isnull=True) | Q(aktif_sampai__gte=today),
        ).filter(
            Q(target="semua")
            | Q(target="divisi", divisi=user.divisi)
            | Q(target="personal", staff_personal=user)
        )[:5]

        # --- Job Aktif ---
        job_aktif = JobBoard.objects.filter(
            pic_staff=user, status_pekerjaan__in=["antrean", "dikerjakan"]
        ).select_related("tahap", "tahap__divisi", "order_item__order")

        job_data = [
            {
                "job_id": j.id,
                "produk": j.order_item.jenis_produk,
                "tahap": j.tahap.nama if j.tahap else "-",
                "divisi": j.tahap.divisi.nama if j.tahap and j.tahap.divisi else "-",
                "status": j.get_status_pekerjaan_display(),
                "order_id": j.order_item.order_id,
            }
            for j in job_aktif
        ]

        return Response(
            {
                "profil": {
                    "id": user.id,
                    "nama": user.get_full_name() or user.username,
                    "username": user.username,
                    "role": user.role,
                    "divisi": user.divisi.nama if user.divisi else None,
                    "foto_profil": (
                        request.build_absolute_uri(user.foto_profil.url)
                        if user.foto_profil
                        else None
                    ),
                },
                "absensi_hari_ini": absensi_data,
                "timecard_bulan_ini": {
                    "bulan": now_month,
                    "tahun": now_year,
                    "total_hadir": total_hadir,
                    "total_jam_kerja": round(total_jam, 2),
                    "total_insentif": total_insentif,
                },
                "kontrak": kontrak_data,
                "pengumuman": AnnouncementSerializer(pengumuman, many=True).data,
                "job_aktif": job_data,
                "total_job_aktif": len(job_data),
            }
        )
