from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

import calendar
from django.utils import timezone

from ..models import CustomUser, Divisi, ShiftTiming, JobBoard
from ..serializers import CustomUserSerializer, DivisiSerializer, ShiftTimingSerializer
from ..permissions import IsOwnerManagerOrAdmin, IsOwnerOrManager, IsOwnerManagerAdminOrReadOnly
from users.models import SecurityAuditLog

class CustomUserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    
    def get_permissions(self):
        if self.action == 'me':
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsOwnerManagerOrAdmin()]

    def check_permissions(self, request):
        super().check_permissions(request)
        # Proteksi hak akses: Staff tidak boleh mengubah data karyawan lain
        # Hanya Owner, Manager, atau Admin yang dapat memodifikasi model karyawan secara umum
        if request.method not in ['GET', 'HEAD', 'OPTIONS']:
            if self.action != 'me':
                if not (request.user and getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']):
                    self.permission_denied(request, message="Hanya Owner, Manager, atau Admin yang dapat memodifikasi data karyawan.")


    def _guard_role_change(self, request, target):
        requested_role = request.data.get('role')
        if requested_role is not None and request.user.role != 'owner':
            return Response({'error': 'Hanya Owner yang dapat mengubah role.'}, status=403)
        if target.role in ('owner', 'manager') and request.user.role != 'owner':
            return Response({'error': 'Hanya Owner yang dapat mengubah akun Owner/Manager.'}, status=403)
        return None

    def update(self, request, *args, **kwargs):
        denied = self._guard_role_change(request, self.get_object())
        return denied or super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        denied = self._guard_role_change(request, self.get_object())
        return denied or super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        denied = self._guard_role_change(request, self.get_object())
        return denied or super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        queryset = CustomUser.objects.all()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    @action(detail=False, methods=['get', 'patch'], url_path='me')
    def me(self, request):
        user = request.user
        if request.method == 'GET':
            serializer = self.get_serializer(user)
            return Response(serializer.data)
        elif request.method == 'PATCH':
            # Proteksi field HR/Admin agar tidak bisa diubah oleh staff secara mandiri
            if hasattr(request.data, '_mutable'):
                data = request.data.copy()
            elif isinstance(request.data, dict):
                data = request.data.copy()
            else:
                data = dict(request.data)

            if request.user.role != 'owner':
                hr_fields = [
                    'username', 'email', 'role', 'divisi', 'status_karyawan', 
                    'jenis_kontrak', 'kontrak_mulai', 'kontrak_selesai', 
                    'no_kpj', 'bpjs_kes', 'file_pkwt', 'nip'
                ]
                for field in hr_fields:
                    if field in data:
                        data.pop(field)

            serializer = self.get_serializer(user, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=400)

    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        # Hanya Owner atau Manager yang boleh mereset password
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Hanya Owner atau Manager yang dapat mereset password.'}, status=status.HTTP_403_FORBIDDEN)
        
        user_to_reset = self.get_object()
        
        # Manager tidak boleh mereset password Owner atau Manager lain
        if request.user.role == 'manager' and user_to_reset.role in ['owner', 'manager']:
            return Response({'error': 'Manager tidak boleh mereset password Owner atau Manager.'}, status=status.HTTP_403_FORBIDDEN)
            
        new_password = request.data.get('new_password')
        if not new_password:
            return Response({'error': 'Password baru wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            validate_password(new_password, user=user_to_reset)
        except DjangoValidationError as e:
            return Response({'error': ", ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)
            
        user_to_reset.set_password(new_password)
        user_to_reset.save()
        
        # Catat audit log
        SecurityAuditLog.objects.create(
            user=request.user,
            username_input=request.user.username,
            event="PASSWORD_CHANGED",
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan=f"Reset password untuk user {user_to_reset.username} oleh {request.user.role}",
            berhasil=True,
        )
        
        return Response({'message': f'Password untuk {user_to_reset.username} berhasil diubah.'}, status=status.HTTP_200_OK)


class CreateUserView(APIView):
    permission_classes = [IsOwnerOrManager]

    def post(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()
        role     = request.data.get('role', 'staff')
        if role in ('owner', 'manager') and request.user.role != 'owner':
            return Response({'error': 'Hanya Owner yang dapat membuat akun Owner/Manager.'}, status=403)
        no_hp    = request.data.get('no_hp', '')
        divisi   = request.data.get('divisi', None)
        first_name = request.data.get('first_name', '')

        # Validasi field wajib
        if not username or not password:
            return Response(
                {'error': 'Username dan password wajib diisi.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Cek duplikat username
        if CustomUser.objects.filter(username=username).exists():
            return Response(
                {'error': f'Username "{username}" sudah digunakan.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buat user baru
        user = CustomUser(
            username=username,
            role=role,
            no_hp=no_hp,
            first_name=first_name,
        )
        if divisi:
            try:
                user.divisi = Divisi.objects.get(pk=divisi)
            except Divisi.DoesNotExist:
                pass

        user.set_password(password)  # Hash password dengan benar
        user.save()

        return Response(
            {
                'message': f'Akun "{username}" berhasil dibuat.',
                'id': user.id,
                'username': user.username,
                'role': user.role,
            },
            status=status.HTTP_201_CREATED
        )


class DivisiViewSet(viewsets.ModelViewSet):
    queryset = Divisi.objects.all()
    serializer_class = DivisiSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]


class ShiftTimingViewSet(viewsets.ModelViewSet):
    queryset = ShiftTiming.objects.all()
    serializer_class = ShiftTimingSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]


class StaffPerformanceReportView(APIView):
    """
    GET /api/reports/staff-performance/
    Mengembalikan statistik kinerja masing-masing staff:
    - Jumlah job diselesaikan, sedang berjalan, gagal, dll.
    - Total insentif yang diperoleh.
    - Rata-rata durasi penyelesaian tugas (menit).
    """
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        
        time_range = request.query_params.get('range', 'bulan_ini')
        now = timezone.localtime(timezone.now())
        
        # Default rentang waktu
        start = None
        end = None
        
        if time_range == 'bulan_ini':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            _, last_day = calendar.monthrange(now.year, now.month)
            end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
        elif time_range == 'bulan_lalu':
            year = now.year
            month = now.month - 1
            if month <= 0:
                month = 12
                year -= 1
            start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
            _, last_day = calendar.monthrange(year, month)
            end = now.replace(year=year, month=month, day=last_day, hour=23, minute=59, second=59, microsecond=999999)

        # Ambil semua user dengan role staff (dioptimasi dengan prefetch_related)
        staff_members = CustomUser.objects.filter(role='staff').select_related('divisi').prefetch_related('my_tasks', 'absensi')
        
        from hr.models import DailyAttendanceSession
        if start and end:
            sessions = DailyAttendanceSession.objects.filter(tanggal__gte=start.date(), tanggal__lte=end.date())
        else:
            sessions = DailyAttendanceSession.objects.all()
        session_map = {s.tanggal: s.batas_maksimal for s in sessions}

        report_data = []
        for staff in staff_members:
            jobs = list(staff.my_tasks.all())
            
            # Saring berdasarkan rentang waktu jika diset
            if start and end:
                completed_jobs = [j for j in jobs if j.status_pekerjaan == 'selesai' and j.waktu_selesai and start <= j.waktu_selesai <= end]
                failed_jobs = [j for j in jobs if j.status_pekerjaan in ['gagal', 'batal'] and j.waktu_selesai and start <= j.waktu_selesai <= end]
            else:
                completed_jobs = [j for j in jobs if j.status_pekerjaan == 'selesai']
                failed_jobs = [j for j in jobs if j.status_pekerjaan in ['gagal', 'batal']]
                
            active_jobs = [j for j in jobs if j.status_pekerjaan == 'dikerjakan']
            pending_jobs = [j for j in jobs if j.status_pekerjaan == 'antrean']
            constraint_jobs = [j for j in jobs if j.status_pekerjaan == 'kendala']

            total_jobs = len(completed_jobs) + len(failed_jobs) + len(active_jobs) + len(pending_jobs) + len(constraint_jobs)
            total_insentif = sum(j.insentif for j in completed_jobs)
            
            # Rata-rata durasi pengerjaan job selesai (menit)
            durations = []
            for j in completed_jobs:
                if j.waktu_mulai and j.waktu_selesai:
                    diff = j.waktu_selesai - j.waktu_mulai
                    durations.append(diff.total_seconds() / 60.0)
            
            avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

            # Hitung statistik kehadiran
            absensi_list = staff.absensi.all()
            if start and end:
                absensi_list = [a for a in absensi_list if start.date() <= a.tanggal <= end.date()]
            
            ontime_count = 0
            late_count = 0
            alpha_count = 0
            
            for a in absensi_list:
                if a.status == 'alpha':
                    alpha_count += 1
                elif a.status in ['hadir', 'wfh', 'izin']:
                    batas = session_map.get(a.tanggal)
                    if batas and a.jam_masuk:
                        if a.jam_masuk > batas:
                            late_count += 1
                        else:
                            ontime_count += 1
                    else:
                        if a.status == 'izin':
                            late_count += 1
                        else:
                            ontime_count += 1
            
            report_data.append({
                'id': staff.id,
                'username': staff.username,
                'nama_lengkap': staff.get_full_name() or staff.username,
                'divisi': staff.divisi.nama if staff.divisi else '-',
                'jobs_total': total_jobs,
                'jobs_completed': len(completed_jobs),
                'jobs_in_progress': len(active_jobs),
                'jobs_pending': len(pending_jobs),
                'jobs_failed': len(failed_jobs),
                'jobs_constraint': len(constraint_jobs),
                'total_insentif': total_insentif,
                'avg_duration_minutes': avg_duration,
                'att_ontime': ontime_count,
                'att_late': late_count,
                'att_alpha': alpha_count,
                'att_total': len(absensi_list)
            })
            
        # Detailed Jobs
        jobs_qs = JobBoard.objects.select_related(
            'order_item', 'order_item__order', 'tahap', 'tahap__divisi', 'pic_staff'
        ).order_by('-waktu_selesai')
        
        if start and end:
            jobs_qs = jobs_qs.filter(waktu_selesai__gte=start, waktu_selesai__lte=end)
        else:
            jobs_qs = jobs_qs.filter(waktu_selesai__isnull=False)[:500]

        detailed_jobs = []
        for j in jobs_qs:
            duration_minutes = 0
            if j.waktu_mulai and j.waktu_selesai:
                diff = j.waktu_selesai - j.waktu_mulai
                duration_minutes = round(diff.total_seconds() / 60.0, 1)

            detailed_jobs.append({
                'id': j.id,
                'order_id': j.order_item.order.id if j.order_item and j.order_item.order else '-',
                'order_nama': j.order_item.order.nama if j.order_item and j.order_item.order else '-',
                'order_tgl': j.order_item.order.waktu.strftime('%Y-%m-%d %H:%M') if j.order_item and j.order_item.order else '-',
                'jenis_produk': j.order_item.jenis_produk if j.order_item else '-',
                'bahan': j.order_item.bahan if j.order_item else '-',
                'qty': j.order_item.qty if j.order_item else 1,
                'tahap': j.tahap.nama if j.tahap else '-',
                'divisi': j.tahap.divisi.nama if j.tahap and j.tahap.divisi else '-',
                'pic_username': j.pic_staff.username if j.pic_staff else 'Unassigned',
                'pic_fullname': j.pic_staff.get_full_name() or j.pic_staff.username if j.pic_staff else 'Unassigned',
                'status': j.status_pekerjaan,
                'waktu_mulai': j.waktu_mulai.strftime('%Y-%m-%d %H:%M') if j.waktu_mulai else '-',
                'waktu_selesai': j.waktu_selesai.strftime('%Y-%m-%d %H:%M') if j.waktu_selesai else '-',
                'durasi_menit': duration_minutes,
                'insentif': j.insentif,
                'biaya_desain': j.biaya_desain
            })

        return Response({
            'range': time_range,
            'data': report_data,
            'detailed_jobs': detailed_jobs
        })

