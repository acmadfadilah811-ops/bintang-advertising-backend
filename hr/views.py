from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import JobBoard
from api.views import IsOwnerOrManager

from .models import Absensi, Kontrak, StaffAnnouncement, DailyAttendanceSession, UnlockRequest, Akun, TransaksiBukuBesar
from .serializers import AbsensiSerializer, AnnouncementSerializer, KontrakSerializer, AkunSerializer, TransaksiBukuBesarSerializer


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

class IsOwnerOrManagerPerm(IsOwnerOrManager):
    """Alias agar tidak konflik nama."""
    pass


def sync_attendance_for_date(target_date):
    """
    Memastikan semua staff aktif memiliki record absensi pada tanggal tersebut.
    Jika belum ada, otomatis dibuat dengan status 'alpha' (Tidak Masuk).
    Untuk tanggal hari ini, hanya dibuat jika batas_maksimal absensi telah terlewati.
    """
    from api.models import CustomUser
    
    today = timezone.localdate()
    if target_date > today:
        return
        
    if target_date == today:
        session = DailyAttendanceSession.objects.filter(tanggal=today).first()
        if not session or timezone.now() <= session.batas_maksimal:
            # Belum melewati batas waktu absen hari ini
            return
            
    active_staff = CustomUser.objects.filter(is_active=True, role__in=["staff", "manager"])
    for member in active_staff:
        Absensi.objects.get_or_create(
            staff=member,
            tanggal=target_date,
            defaults={"status": "alpha"}
        )



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
        session = DailyAttendanceSession.objects.filter(tanggal=today, is_active=True).first()
        if not session:
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
                for d in range(1, end_day + 1):
                    sync_attendance_for_date(date(t_int, b_int, d))

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
            for d in range(1, end_day + 1):
                sync_attendance_for_date(date(tahun, bulan, d))

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

        # --- Status Lock & Sesi Absensi ---
        sesi_hari_ini = DailyAttendanceSession.objects.filter(tanggal=today).first()
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
        today = timezone.localdate()
        sesi = DailyAttendanceSession.objects.filter(tanggal=today).first()
        if not sesi:
            return Response({"is_active": False, "detail": "Belum ada sesi hari ini."})
        return Response({
            "is_active": sesi.is_active,
            "waktu_mulai": sesi.waktu_mulai,
            "batas_maksimal": sesi.batas_maksimal,
        })

    def post(self, request):
        if request.user.role not in ("owner", "manager"):
            return Response({"detail": "Hanya Owner/Manager yang bisa memulai sesi."}, status=403)
        
        batas_waktu_str = request.data.get("batas_maksimal") # Format HH:MM
        waktu_mulai_str = request.data.get("waktu_mulai") # Format HH:MM
        
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

        sesi, created = DailyAttendanceSession.objects.get_or_create(
            tanggal=today,
            defaults={
                "waktu_mulai": waktu_mulai,
                "batas_maksimal": batas_maksimal,
                "dihidupkan_oleh": request.user,
                "is_active": True
            }
        )
        if not created:
            sesi.waktu_mulai = waktu_mulai
            sesi.batas_maksimal = batas_maksimal
            sesi.is_active = True
            sesi.save()
            
        return Response({
            "detail": "Sesi absensi berhasil diperbarui.", 
            "waktu_mulai": sesi.waktu_mulai,
            "batas_maksimal": sesi.batas_maksimal
        })


class UnlockRequestStaffView(APIView):
    """
    POST /api/hr/unlock-request/
    Staff mengirim alasan keterlambatan/minta buka akun.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        today = timezone.localdate()
        sesi = DailyAttendanceSession.objects.filter(tanggal=today, is_active=True).first()
        
        if not sesi:
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
        req = UnlockRequest.objects.filter(pk=pk).first()
        if not req:
            return Response({"detail": "Permintaan tidak ditemukan."}, status=404)
            
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
        
        # Contoh perhitungan saldo awal (Debit - Kredit)
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

