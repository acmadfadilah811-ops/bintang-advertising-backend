from django.db.models import Q, Sum
from django.utils import timezone
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action

from api.models import JobBoard
from api.views import IsOwnerOrManager

from .models import Absensi, Kontrak, StaffAnnouncement, DailyAttendanceSession, UnlockRequest, Akun, TransaksiBukuBesar, SlipGaji
from .serializers import AbsensiSerializer, AnnouncementSerializer, KontrakSerializer, AkunSerializer, TransaksiBukuBesarSerializer, SlipGajiSerializer
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

class IsOwnerOrManagerPerm(IsOwnerOrManager):
    """Alias agar tidak konflik nama."""
    pass


def get_or_create_daily_session():
    import os
    import json
    from django.conf import settings
    from datetime import datetime, timedelta
    from api.models import SystemConfig
    
    today = timezone.localdate()
    sesi = DailyAttendanceSession.objects.filter(tanggal=today).first()
    if not sesi:
        # Coba baca dari SystemConfig terlebih dahulu
        try:
            jam_masuk_str = SystemConfig.objects.get(key="payroll_jam_masuk").value
            toleransi_str = SystemConfig.objects.get(key="payroll_toleransi_menit").value
            toleransi_menit = int(toleransi_str)
            
            mulai_time_obj = datetime.strptime(jam_masuk_str, "%H:%M").time()
            waktu_mulai = timezone.make_aware(datetime.combine(today, mulai_time_obj))
            batas_maksimal = waktu_mulai + timedelta(minutes=toleransi_menit)
            
            sesi = DailyAttendanceSession.objects.create(
                tanggal=today,
                waktu_mulai=waktu_mulai,
                batas_maksimal=batas_maksimal,
                is_active=True
            )
            return sesi
        except Exception:
            pass

        # Fallback ke file JSON schedule lama jika SystemConfig belum terkonfigurasi/error
        config_path = os.path.join(settings.BASE_DIR, "hr_attendance_schedule.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                
                if config.get("repeat_daily", False):
                    mulai_time_obj = datetime.strptime(config["waktu_mulai"], "%H:%M").time()
                    waktu_mulai = timezone.make_aware(datetime.combine(today, mulai_time_obj))
                    
                    batas_time_obj = datetime.strptime(config["batas_maksimal"], "%H:%M").time()
                    batas_maksimal = timezone.make_aware(datetime.combine(today, batas_time_obj))
                    
                    sesi = DailyAttendanceSession.objects.create(
                        tanggal=today,
                        waktu_mulai=waktu_mulai,
                        batas_maksimal=batas_maksimal,
                        is_active=True
                    )
            except Exception as e:
                logger.error(f"Error auto-creating session from schedule: {e}", exc_info=True)
    return sesi


def sync_attendance_for_range(start_date, end_date):
    """
    Memastikan semua staff aktif memiliki record absensi pada range tanggal tersebut.
    Jika belum ada, otomatis dibuat dengan status 'alpha' (Tidak Masuk) menggunakan bulk_create.
    """
    from api.models import CustomUser
    from datetime import date, timedelta
    
    today = timezone.localdate()
    
    # Generate list tanggal dari start_date sampai end_date
    delta = end_date - start_date
    target_dates = []
    for i in range(delta.days + 1):
        target_date = start_date + timedelta(days=i)
        if target_date > today:
            continue
        if target_date == today:
            session = get_or_create_daily_session()
            if not session or timezone.now() <= session.batas_maksimal:
                # Belum melewati batas waktu absen hari ini, abaikan hari ini
                continue
        target_dates.append(target_date)
        
    if not target_dates:
        return
        
    active_staff = list(CustomUser.objects.filter(is_active=True, role__in=["staff", "manager"]))
    if not active_staff:
        return

    # Ambil semua data absensi yang sudah ada untuk range tanggal ini & staff aktif
    existing_absensi = set(
        Absensi.objects.filter(
            staff__in=active_staff,
            tanggal__range=(start_date, end_date)
        ).values_list('staff_id', 'tanggal')
    )

    # Siapkan list bulk_create
    to_create = []
    for target_date in target_dates:
        for member in active_staff:
            if (member.id, target_date) not in existing_absensi:
                to_create.append(
                    Absensi(
                        staff=member,
                        tanggal=target_date,
                        status="alpha"
                    )
                )

    if to_create:
        Absensi.objects.bulk_create(to_create, ignore_conflicts=True)


def sync_attendance_for_date(target_date):
    sync_attendance_for_range(target_date, target_date)



# ---------------------------------------------------------------------------
# 1. ABSENSI — Clock-in / Clock-out
# ---------------------------------------------------------------------------

class ClockInView(APIView):
    """
    POST /api/hr/absensi/clock-in/
    Staff menekan tombol MASUK. Membuat record Absensi untuk hari ini.
    Mengecek apakah sesi absensi aktif dan belum melewati batas maksimal.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        today = timezone.localdate()
        now = timezone.now()

        # 1. Cek Sesi Absensi Aktif
        session = get_or_create_daily_session()
        if not session or not session.is_active:
            return Response(
                {"detail": "Sesi absensi hari ini belum dibuka oleh Manager/Owner."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Cek Batas Maksimal & Izin Keterlambatan
        if now > session.batas_maksimal:
            # Cek apakah punya UnlockRequest yang di-approve hari ini
            approved_request = UnlockRequest.objects.filter(
                staff=request.user, sesi=session, status="approved"
            ).exists()
            
            if not approved_request:
                return Response(
                    {"detail": "Batas waktu absen sudah lewat. Akun terkunci, silakan ajukan izin."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # 3. Cek apakah sudah clock-in
        existing = Absensi.objects.filter(staff=request.user, tanggal=today).first()
        if existing and existing.status != "alpha":
            return Response(
                {
                    "detail": "Anda sudah clock-in hari ini.",
                    "jam_masuk": existing.jam_masuk,
                    "sudah_clock_out": existing.sudah_clock_out,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4. Buat / Update Absensi
        status_hadir = "hadir"
        if now > session.batas_maksimal:
            status_hadir = "izin" # Bisa disesuaikan jika terlambat dihitung izin/terlambat

        if existing and existing.status == "alpha":
            absensi = existing
            absensi.jam_masuk = now
            absensi.status = status_hadir
            if request.data.get("catatan"):
                absensi.catatan = request.data["catatan"]
            absensi.save()
        else:
            absensi = Absensi.objects.create(
                staff=request.user,
                tanggal=today,
                jam_masuk=now,
                status=status_hadir,
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
        ).exclude(status="alpha").first()

        if not absensi or not absensi.jam_masuk:
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
        tanggal = request.query_params.get("tanggal")
        bulan = request.query_params.get("bulan", timezone.localdate().month)
        tahun = request.query_params.get("tahun", timezone.localdate().year)

        if tanggal:
            from datetime import datetime
            try:
                if isinstance(tanggal, str):
                    query_date = datetime.strptime(tanggal, "%Y-%m-%d").date()
                else:
                    query_date = tanggal
            except ValueError:
                query_date = timezone.localdate()
            sync_attendance_for_date(query_date)
            base_qs = Absensi.objects.filter(tanggal=tanggal)
        else:
            today = timezone.localdate()
            try:
                b_int = int(bulan)
                t_int = int(tahun)
            except ValueError:
                b_int = today.month
                t_int = today.year
            
            import calendar
            _, last_day = calendar.monthrange(t_int, b_int)
            
            if t_int < today.year or (t_int == today.year and b_int <= today.month):
                end_day = today.day if (t_int == today.year and b_int == today.month) else last_day
                from datetime import date
                sync_attendance_for_range(date(t_int, b_int, 1), date(t_int, b_int, end_day))

            base_qs = Absensi.objects.filter(tanggal__month=b_int, tanggal__year=t_int)

        if user.role in ("owner", "manager"):
            qs = base_qs.select_related("staff", "staff__divisi")
            staff_id = request.query_params.get("staff_id")
            if staff_id:
                qs = qs.filter(staff_id=staff_id)
        else:
            qs = base_qs.filter(staff=user)

        serializer = AbsensiSerializer(qs, many=True)
        return Response(serializer.data)


class AbsensiDetailView(APIView):
    """
    PATCH /api/hr/absensi/{id}/
    Manager/Owner: memperbarui status atau catatan keterangan kehadiran staff secara manual.
    """
    permission_classes = [IsOwnerOrManagerPerm]

    def patch(self, request, pk):
        absensi = Absensi.objects.filter(pk=pk).first()
        if not absensi:
            return Response({"detail": "Absensi tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AbsensiSerializer(absensi, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


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

        # Sync attendance harian untuk seluruh hari di bulan tersebut up to today
        today = timezone.localdate()
        import calendar
        _, last_day = calendar.monthrange(tahun, bulan)
        
        if tahun < today.year or (tahun == today.year and bulan <= today.month):
            end_day = today.day if (tahun == today.year and bulan == today.month) else last_day
            from datetime import date
            sync_attendance_for_range(date(tahun, bulan, 1), date(tahun, bulan, end_day))

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

class AnnouncementDetailView(APIView):
    permission_classes = [IsOwnerOrManagerPerm]

    def delete(self, request, pk):
        announcement = StaffAnnouncement.objects.filter(pk=pk).first()
        if not announcement:
            return Response({"detail": "Pengumuman tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)
        
        announcement.delete()
        return Response({"detail": "Pengumuman berhasil dihapus."}, status=status.HTTP_204_NO_CONTENT)

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
            else {"status": "belum_absen", "jam_masuk": None, "jam_keluar": None, "workspace_unlocked": False}
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

        pengumuman = StaffAnnouncement.objects.filter(
            Q(aktif_sampai__isnull=True) | Q(aktif_sampai__gte=today),
        ).filter(
            Q(target="semua")
            | Q(target="divisi", divisi=user.divisi)
            | Q(target="personal", staff_personal=user)
        )[:5]

        # --- Total Job Selesai & Gagal ---
        total_job_selesai = JobBoard.objects.filter(pic_staff=user, status_pekerjaan="selesai").count()
        total_job_gagal = JobBoard.objects.filter(pic_staff=user, status_pekerjaan="gagal").count()

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

        # --- Status Lock & Sesi Absensi ---
        sesi_hari_ini = get_or_create_daily_session()
        is_locked = False
        unlock_request = None
        sesi_aktif = sesi_hari_ini is not None and sesi_hari_ini.is_active

        if user.role == "staff":
            if sesi_aktif:
                now = timezone.now()
                # Terkunci jika sudah lewat batas maksimal, belum absen (atau status alpha), dan tidak punya izin approved
                has_checked_in = absensi_hari_ini is not None and absensi_hari_ini.status != "alpha"
                if now > sesi_hari_ini.batas_maksimal and not has_checked_in:
                    approved_req = UnlockRequest.objects.filter(staff=user, sesi=sesi_hari_ini, status="approved").exists()
                    if not approved_req:
                        is_locked = True
                        req = UnlockRequest.objects.filter(staff=user, sesi=sesi_hari_ini).order_by('-waktu_request').first()
                        if req:
                            unlock_request = {
                                "id": req.id,
                                "status": req.status,
                                "alasan": req.alasan
                            }

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
                "status_terkunci": {
                    "is_locked": is_locked,
                    "sesi_aktif": sesi_aktif,
                    "batas_maksimal": sesi_hari_ini.batas_maksimal if sesi_hari_ini else None,
                    "unlock_request": unlock_request,
                    "workspace_unlocked": absensi_hari_ini.workspace_unlocked if absensi_hari_ini else False,
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
                "total_job_selesai": total_job_selesai,
                "total_job_gagal": total_job_gagal,
            }
        )

# ---------------------------------------------------------------------------
# 6. SESSION MANAGER & UNLOCK REQUESTS
# ---------------------------------------------------------------------------

class AttendanceSessionManagerView(APIView):
    """
    GET /api/hr/attendance-session/ -> Lihat status sesi hari ini
    POST /api/hr/attendance-session/ -> Mulai sesi (Owner/Manager)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sesi = get_or_create_daily_session()
        if not sesi:
            return Response({"is_active": False, "detail": "Belum ada sesi hari ini."})
            
        import os
        import json
        from django.conf import settings
        config_path = os.path.join(settings.BASE_DIR, "hr_attendance_schedule.json")
        repeat_daily = False
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                repeat_daily = config.get("repeat_daily", False)
            except Exception:
                pass

        return Response({
            "is_active": sesi.is_active,
            "waktu_mulai": sesi.waktu_mulai,
            "batas_maksimal": sesi.batas_maksimal,
            "repeat_daily": repeat_daily,
        })

    def post(self, request):
        if request.user.role not in ("owner", "manager"):
            return Response({"detail": "Hanya Owner/Manager yang bisa memulai sesi."}, status=403)
        
        batas_waktu_str = request.data.get("batas_maksimal") # Format HH:MM
        waktu_mulai_str = request.data.get("waktu_mulai") # Format HH:MM
        repeat_daily = request.data.get("repeat_daily", False)
        
        if not batas_waktu_str or not waktu_mulai_str:
            return Response({"detail": "waktu_mulai dan batas_maksimal wajib diisi (HH:MM)."}, status=400)
            
        today = timezone.localdate()
        
        # Parse waktu_mulai dan batas_maksimal
        from datetime import datetime
        try:
            mulai_time_obj = datetime.strptime(waktu_mulai_str, "%H:%M").time()
            waktu_mulai = timezone.make_aware(datetime.combine(today, mulai_time_obj))
            
            batas_time_obj = datetime.strptime(batas_waktu_str, "%H:%M").time()
            batas_maksimal = timezone.make_aware(datetime.combine(today, batas_time_obj))
        except ValueError:
            return Response({"detail": "Format waktu salah. Gunakan HH:MM"}, status=400)

        # Simpan config schedule jika repeat_daily=True/False
        import os
        import json
        from django.conf import settings
        config_path = os.path.join(settings.BASE_DIR, "hr_attendance_schedule.json")
        try:
            with open(config_path, "w") as f:
                json.dump({
                    "waktu_mulai": waktu_mulai_str,
                    "batas_maksimal": batas_waktu_str,
                    "repeat_daily": repeat_daily
                }, f)
        except Exception as e:
            logger.error(f"Error writing attendance schedule json: {e}", exc_info=True)

        sesi = DailyAttendanceSession.objects.filter(tanggal=today).first()
        if sesi:
            sesi.waktu_mulai = waktu_mulai
            sesi.batas_maksimal = batas_maksimal
            sesi.is_active = True
            sesi.dihidupkan_oleh = request.user
            sesi.save()
        else:
            sesi = DailyAttendanceSession.objects.create(
                tanggal=today,
                waktu_mulai=waktu_mulai,
                batas_maksimal=batas_maksimal,
                dihidupkan_oleh=request.user,
                is_active=True
            )
            
        return Response({
            "is_active": sesi.is_active,
            "waktu_mulai": sesi.waktu_mulai,
            "batas_maksimal": sesi.batas_maksimal,
            "repeat_daily": repeat_daily,
            "detail": "Sesi absensi berhasil diperbarui."
        })


class NotifyLateStaffView(APIView):
    """
    POST /api/hr/attendance-session/notify-late/
    Owner/Manager: Mencari staff yang belum clock-in melewati batas_maksimal
    dan mengirim notifikasi WA otomatis untuk menanyakan alasan mereka.
    """
    permission_classes = [IsOwnerOrManagerPerm]

    def post(self, request):
        today = timezone.localdate()
        session = DailyAttendanceSession.objects.filter(tanggal=today).first()
        if not session or not session.is_active:
            return Response({"detail": "Tidak ada sesi absensi aktif untuk hari ini."}, status=400)
            
        from api.models import CustomUser
        from hr.models import Absensi, UnlockRequest
        from api.whatsapp_client import whatsapp_client
        
        # Get all active staff
        active_staff = CustomUser.objects.filter(is_active=True, role='staff')
        
        notified_count = 0
        for staff in active_staff:
            # Check if they clocked in today
            absensi = Absensi.objects.filter(staff=staff, tanggal=today).first()
            has_checked_in = absensi is not None and absensi.status not in ('alpha', 'izin', 'sakit')
            
            # If they haven't checked in, they are considered late/absent
            if not has_checked_in:
                # Check if we already created an UnlockRequest for them today
                req, created = UnlockRequest.objects.get_or_create(
                    staff=staff,
                    sesi=session,
                    defaults={
                        'alasan': 'Belum memberikan alasan (Menunggu balasan WhatsApp)'
                    }
                )
                
                # Send WhatsApp message
                if staff.no_wa:
                    clean_number = staff.no_wa.replace('+', '').replace(' ', '').replace('-', '')
                    msg = (
                        f"Halo {staff.get_full_name() or staff.username},\n\n"
                        f"Anda terdeteksi belum melakukan absensi masuk (Clock-in) untuk hari ini "
                        f"({today.strftime('%d-%m-%Y')}) hingga batas waktu "
                        f"{timezone.localtime(session.batas_maksimal).strftime('%H:%M')} WIB.\n\n"
                        f"Akses akun Anda sementara dikunci. Silakan *balas pesan ini* dengan memberikan "
                        f"alasan keterlambatan atau ketidakhadiran Anda untuk mengajukan pembukaan akses kepada Manager."
                    )
                    try:
                        whatsapp_client.send_text(clean_number, msg)
                        notified_count += 1
                    except Exception as e:
                        logger.error(f"Gagal mengirim notifikasi absensi ke {staff.username}: {e}")
                        
        return Response({
            "detail": f"Berhasil mengirim notifikasi keterlambatan/absen ke {notified_count} staff.",
            "notified_count": notified_count
        })


class UnlockRequestStaffView(APIView):
    """
    POST /api/hr/unlock-request/
    Staff mengirim alasan keterlambatan/minta buka akun.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        sesi = get_or_create_daily_session()
        
        if not sesi or not sesi.is_active:
            return Response({"detail": "Sesi absensi belum dimulai."}, status=400)
            
        alasan = request.data.get("alasan")
        if not alasan:
            return Response({"detail": "Alasan wajib diisi."}, status=400)
            
        req = UnlockRequest.objects.create(
            staff=request.user,
            sesi=sesi,
            alasan=alasan
        )
        
        return Response({"detail": "Permintaan berhasil dikirim. Menunggu persetujuan Manager.", "id": req.id}, status=201)


class UnlockRequestManagerView(APIView):
    """
    GET /api/hr/unlock-requests/ -> Lihat daftar pending
    POST /api/hr/unlock-requests/<id>/approve/
    POST /api/hr/unlock-requests/<id>/reject/
    """
    permission_classes = [IsOwnerOrManagerPerm]

    def get(self, request):
        today = timezone.localdate()
        reqs = UnlockRequest.objects.filter(sesi__tanggal=today, status="pending").select_related("staff", "sesi")
        data = [
            {
                "id": r.id,
                "staff": r.staff.username,
                "staff_nama": r.staff.get_full_name(),
                "staff_no_wa": r.staff.no_wa,
                "alasan": r.alasan,
                "waktu_request": r.waktu_request,
                "status": r.status
            }
            for r in reqs
        ]
        return Response(data)

class UnlockRequestActionView(APIView):
    permission_classes = [IsOwnerOrManagerPerm]
    
    def post(self, request, pk, action):
        from django.db import transaction
        
        with transaction.atomic():
            req = UnlockRequest.objects.select_for_update().filter(pk=pk).first()
            if not req:
                return Response({"detail": "Permintaan tidak ditemukan."}, status=404)
                
            if req.status != "pending":
                return Response({"detail": f"Permintaan sudah diproses sebelumnya (Status saat ini: {req.status})."}, status=400)
                
            if action == "approve":
                req.status = "approved"
            elif action == "reject":
                req.status = "rejected"
            else:
                return Response({"detail": "Action tidak valid."}, status=400)
                
            req.direspon_oleh = request.user
            req.waktu_respon = timezone.now()
            req.save()
        
        return Response({"detail": f"Permintaan berhasil di-{action}.", "status": req.status})


# ---------------------------------------------------------------------------
# 7. FINANCE & BUKU BESAR
# ---------------------------------------------------------------------------

class AkunViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/finance/akun/
    """
    queryset = Akun.objects.all().order_by('kode_akun')
    serializer_class = AkunSerializer
    permission_classes = [IsAuthenticated]


class TransaksiBukuBesarViewSet(viewsets.ModelViewSet):
    """
    POST /api/finance/transaksi/
    PATCH /api/finance/transaksi/{id}/
    DELETE /api/finance/transaksi/{id}/
    """
    queryset = TransaksiBukuBesar.objects.all()
    serializer_class = TransaksiBukuBesarSerializer
    permission_classes = [IsOwnerOrManagerPerm]


class BukuBesarView(APIView):
    """
    GET /api/finance/buku-besar/?akun_id=...&start_date=...&end_date=...
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        akun_id = request.query_params.get("akun_id")
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        if not akun_id or not start_date_str or not end_date_str:
            return Response(
                {"detail": "akun_id, start_date, dan end_date wajib diisi."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from datetime import datetime
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"detail": "Format tanggal salah. Gunakan YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1. Hitung saldo awal (sebelum start_date)
        # Asumsi saldo awal = total debit - total kredit sebelum start_date
        # (Idealnya tiap kategori akun punya aturan normal balance yang berbeda,
        # misal Akun Asset -> debit+, kredit-. Akun Kewajiban -> kredit+, debit-.
        # Di sini kita gunakan simple aggregasi).
        
        akun = Akun.objects.filter(id=akun_id).first()
        if not akun:
            return Response({"detail": "Akun tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)

        riwayat_sebelumnya = TransaksiBukuBesar.objects.filter(akun=akun, tanggal__lt=start_date)
        agregat = riwayat_sebelumnya.aggregate(total_debit=Sum('debit'), total_kredit=Sum('kredit'))
        total_debit = agregat['total_debit'] or 0
        total_kredit = agregat['total_kredit'] or 0
        
        # Penentuan saldo awal berdasarkan normal balance kategori akun:
        # Aset & Beban = Debit - Kredit
        # Kewajiban (Utang), Ekuitas (Modal), Pendapatan = Kredit - Debit
        kategori_lower = akun.kategori.lower() if akun.kategori else ""
        is_credit_normal = any(k in kategori_lower for k in ['kewajiban', 'utang', 'ekuitas', 'modal', 'pendapatan', 'revenue'])
        
        if is_credit_normal:
            saldo_awal = total_kredit - total_debit
        else:
            saldo_awal = total_debit - total_kredit

        # 2. Ambil transaksi pada rentang tanggal
        transaksi = TransaksiBukuBesar.objects.filter(
            akun=akun, 
            tanggal__gte=start_date, 
            tanggal__lte=end_date
        ).order_by('tanggal', 'waktu_input')

        return Response({
            "saldo_awal": saldo_awal,
            "transaksi": TransaksiBukuBesarSerializer(transaksi, many=True).data
        })


# ---------------------------------------------------------------------------
# 9. SLIP GAJI VIEWSET — Penggajian Bulanan Otomatis & Jurnal
# ---------------------------------------------------------------------------
class SlipGajiViewSet(viewsets.ModelViewSet):
    queryset = SlipGaji.objects.select_related("staff", "dibayar_oleh").order_by("-tahun", "-bulan", "staff__username")
    serializer_class = SlipGajiSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        # Staff biasa hanya bisa lihat slip miliknya sendiri
        if self.request.user.role not in ("owner", "manager"):
            qs = qs.filter(staff=self.request.user)
        else:
            staff_id = self.request.query_params.get("staff_id")
            if staff_id:
                qs = qs.filter(staff_id=staff_id)
        
        bulan = self.request.query_params.get("bulan")
        tahun = self.request.query_params.get("tahun")
        if bulan:
            qs = qs.filter(bulan=int(bulan))
        if tahun:
            qs = qs.filter(tahun=int(tahun))
        return qs

    @action(detail=False, methods=["post"], url_path="generate")
    def generate_monthly_payroll(self, request):
        """
        POST /api/hr/slip-gaji/generate/
        Mengambil semua staff aktif, menghitung gaji pokok, insentif job, biaya desain,
        dan potongan terlambat absen pada bulan/tahun terkait.
        Format payload: {"bulan": 6, "tahun": 2026}
        """
        if request.user.role not in ("owner", "manager"):
            return Response({"detail": "Akses ditolak."}, status=status.HTTP_403_FORBIDDEN)

        bulan = request.data.get("bulan")
        tahun = request.data.get("tahun")
        if not bulan or not tahun:
            return Response({"detail": "bulan dan tahun wajib diisi."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            bulan = int(bulan)
            tahun = int(tahun)
        except ValueError:
            return Response({"detail": "Format bulan/tahun tidak valid."}, status=status.HTTP_400_BAD_REQUEST)

        from api.models import CustomUser, JobBoard, SystemConfig
        
        # Get late penalty setting
        try:
            penalty_config = SystemConfig.objects.get(key="biaya_potongan_terlambat").value
            biaya_potongan_terlambat = int(penalty_config)
        except Exception:
            biaya_potongan_terlambat = 20000  # Default 20rb per terlambat

        active_staff = CustomUser.objects.filter(is_active=True).exclude(role="owner")
        generated_count = 0
        
        for member in active_staff:
            # 1. Gaji Pokok dari Kontrak Aktif
            kontrak = Kontrak.objects.filter(staff=member, status="aktif").first()
            gaji_pokok = kontrak.gaji_pokok if kontrak else 0

            # 2. Total Insentif & Biaya Desain dari JobBoard
            jobs_done = JobBoard.objects.filter(
                pic_staff=member,
                status_pekerjaan="selesai",
                waktu_selesai__month=bulan,
                waktu_selesai__year=tahun
            )
            agg_jobs = jobs_done.aggregate(
                tot_insentif=Sum("insentif"),
                tot_desain=Sum("biaya_desain")
            )
            total_insentif = agg_jobs["tot_insentif"] or 0
            total_biaya_desain = agg_jobs["tot_desain"] or 0

            # 3. Potongan Terlambat
            absensi_qs = Absensi.objects.filter(
                staff=member,
                tanggal__month=bulan,
                tanggal__year=tahun,
                jam_masuk__isnull=False
            )
            
            late_count = 0
            for absensi in absensi_qs:
                sesi = DailyAttendanceSession.objects.filter(tanggal=absensi.tanggal).first()
                if sesi and absensi.jam_masuk > sesi.batas_maksimal:
                    late_count += 1
            
            potongan_terlambat = late_count * biaya_potongan_terlambat

            # Gaji bersih
            total_gaji_bersih = max(0, gaji_pokok + total_insentif + total_biaya_desain - potongan_terlambat)

            # Simpan atau update jika status masih draft
            slip, created = SlipGaji.objects.get_or_create(
                staff=member,
                bulan=bulan,
                tahun=tahun,
                defaults={
                    "gaji_pokok": gaji_pokok,
                    "total_insentif": total_insentif,
                    "total_biaya_desain": total_biaya_desain,
                    "potongan_terlambat": potongan_terlambat,
                    "total_gaji_bersih": total_gaji_bersih,
                    "status": "draft",
                }
            )

            if not created and slip.status == "draft":
                slip.gaji_pokok = gaji_pokok
                slip.total_insentif = total_insentif
                slip.total_biaya_desain = total_biaya_desain
                slip.potongan_terlambat = potongan_terlambat
                slip.total_gaji_bersih = total_gaji_bersih
                slip.save()

            generated_count += 1

        return Response({"detail": f"Berhasil menghitung & memperbarui payroll untuk {generated_count} staff."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="pay")
    def pay_payroll(self, request, pk=None):
        """
        POST /api/hr/slip-gaji/{id}/pay/
        Membayar slip gaji, menandai status paid, dan memposting jurnal otomatis ke Buku Besar.
        """
        if request.user.role not in ("owner", "manager"):
            return Response({"detail": "Hanya Owner/Manager yang dapat menyetujui pembayaran gaji."}, status=status.HTTP_403_FORBIDDEN)

        slip = self.get_object()
        if slip.status == "paid":
            return Response({"detail": "Slip gaji ini sudah dibayar sebelumnya."}, status=status.HTTP_400_BAD_REQUEST)

        # Update status slip
        with transaction.atomic():
            slip.status = "paid"
            slip.waktu_dibayar = timezone.now()
            slip.dibayar_oleh = request.user
            slip.save()

            # Posting Buku Besar otomatis
            try:
                akun_beban_gaji, _ = Akun.objects.get_or_create(
                    kode_akun='5-2000',
                    defaults={'nama_akun': 'Beban Gaji Karyawan', 'kategori': 'Beban'}
                )
                akun_kas_bank, _ = Akun.objects.get_or_create(
                    kode_akun='1-1001',
                    defaults={'nama_akun': 'Kas di Bank (Pembayaran Gaji)', 'kategori': 'Aset'}
                )
                
                ref_no = f"SG-{slip.id}"
                ket_tx = f"Pembayaran Gaji {slip.staff.username} Periode {slip.bulan}/{slip.tahun}"
                
                # DEBIT Beban Gaji
                TransaksiBukuBesar.objects.create(
                    akun=akun_beban_gaji,
                    tanggal=timezone.localdate(),
                    no_referensi=ref_no,
                    keterangan=ket_tx,
                    debit=slip.total_gaji_bersih,
                    kredit=0
                )
                
                # KREDIT Kas/Bank
                TransaksiBukuBesar.objects.create(
                    akun=akun_kas_bank,
                    tanggal=timezone.localdate(),
                    no_referensi=ref_no,
                    keterangan=ket_tx,
                    debit=0,
                    kredit=slip.total_gaji_bersih
                )
            except Exception as e:
                print(f"Gagal memposting jurnal gaji otomatis: {e}")

        return Response(SlipGajiSerializer(slip).data, status=status.HTTP_200_OK)

