from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, BasePermission, AllowAny
from rest_framework.decorators import action
from rest_framework.throttling import AnonRateThrottle
from django.db.models import Count, Sum, Q, F
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404
import uuid
import re as _re
import logging
from ..models import (

    Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard,
    InventoryItem, RestockHistory, ProductPrice, SystemConfig, FAQ,
    OrderActivityLog, KomplainOrder, KomplainLog, CustomerActivity,
    BillOfMaterials, BoMItem, ShiftTiming, POSAntrianDevice, SaldoKasHarian,
    RingkasanShift
)
from ..serializers import (
    DivisiSerializer, TahapProsesSerializer, CustomUserSerializer,
    ContactSerializer, OrderSerializer, OrderItemSerializer, JobBoardSerializer,
    InventoryItemSerializer, ProductPriceSerializer, SystemConfigSerializer, FAQSerializer,
    BusinessSettingsSerializer, KomplainOrderSerializer, KomplainLogSerializer,
    CustomerActivitySerializer, BillOfMaterialsSerializer, BoMItemSerializer,
    ShiftTimingSerializer, POSAntrianDeviceSerializer, SaldoKasHarianSerializer,
    RingkasanShiftSerializer
)
import os
import calendar
from django.core.cache import cache
from django.db.models import OuterRef, Subquery, Max
from django.db.models.functions import Coalesce
from hr.models import Akun, TransaksiBukuBesar
from users.models import SecurityAuditLog
from ..whatsapp_client import whatsapp_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# IMPORT CUSTOM PERMISSIONS
# ---------------------------------------------------------
from ..permissions import (
    IsOwnerOrManager, IsClockedIn, IsOwnerManagerOrAdmin,
    IsOwnerManagerAdminOrKasir, IsOwnerManagerAdminOrReadOnly
)

class DivisiViewSet(viewsets.ModelViewSet):
    queryset = Divisi.objects.all()
    serializer_class = DivisiSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class TahapProsesViewSet(viewsets.ModelViewSet):
    queryset = TahapProses.objects.all()
    serializer_class = TahapProsesSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

class ShiftTimingViewSet(viewsets.ModelViewSet):
    queryset = ShiftTiming.objects.all()
    serializer_class = ShiftTimingSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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

            if request.user.role not in ['owner', 'manager', 'admin']:
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
            
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
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




# ---------------------------------------------------------
# INVENTORY VIEWSET
# ---------------------------------------------------------







# ---------------------------------------------------------
# JOB MATERIAL DEDUCT VIEW — Kurangi stok saat finishing
# ---------------------------------------------------------


# CATATAN: InventoryItemViewSet sudah didefinisikan lengkap di atas (baris ~126).
# Duplikat class yang lebih simpel ini dihapus agar get_queryset filter & restock @action aktif.

class ProductPriceViewSet(viewsets.ModelViewSet):
    queryset = ProductPrice.objects.all()
    serializer_class = ProductPriceSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    @action(detail=False, methods=['post'], url_path='seed')
    def seed_prices(self, request):
        import json
        from django.conf import settings
        
        path = os.path.join(settings.BASE_DIR, '..', 'bintang_advertising_app', 'data', 'db_harga.json')
        if not os.path.exists(path):
            path = os.path.join(settings.BASE_DIR, 'db_harga.json')
            
        if not os.path.exists(path):
            return Response({"detail": "File db_harga.json tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)
            
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Hapus data lama
        ProductPrice.objects.all().delete()
        
        created_count = 0
        for cat_key, cat_val in data.items():
            for prod_name, prod_val in cat_val.items():
                if isinstance(prod_val, str):
                    clean_price = int(float(prod_val.replace('.', '')))
                    ProductPrice.objects.create(
                        kategori=cat_key,
                        nama_produk=prod_name,
                        harga=clean_price,
                        price_type='flat'
                    )
                    created_count += 1
                elif isinstance(prod_val, dict):
                    keys = list(prod_val.keys())
                    is_qty_tier = any('lbr' in k.lower() or 'pcs' in k.lower() or 'box' in k.lower() or '>' in k.lower() for k in keys)
                    
                    if is_qty_tier:
                        cleaned_tiers = {}
                        for tk, tv in prod_val.items():
                            cleaned_tiers[tk] = int(float(tv.replace('.', '')))
                        ProductPrice.objects.create(
                            kategori=cat_key,
                            nama_produk=prod_name,
                            price_type='tiered',
                            tiers=cleaned_tiers
                        )
                        created_count += 1
                    else:
                        for mat_name, mat_val in prod_val.items():
                            if isinstance(mat_val, str):
                                clean_price = int(float(mat_val.replace('.', '')))
                                ProductPrice.objects.create(
                                    kategori=cat_key,
                                    nama_produk=prod_name,
                                    material=mat_name,
                                    harga=clean_price,
                                    price_type='flat'
                                )
                                created_count += 1
                            elif isinstance(mat_val, dict):
                                cleaned_tiers = {}
                                for tk, tv in mat_val.items():
                                    cleaned_tiers[tk] = int(float(tv.replace('.', '')))
                                ProductPrice.objects.create(
                                    kategori=cat_key,
                                    nama_produk=prod_name,
                                    material=mat_name,
                                    price_type='tiered',
                                    tiers=cleaned_tiers
                                )
                                created_count += 1
                                
        return Response({"detail": f"Berhasil mengimpor {created_count} produk dari db_harga.json."})







# ---------------------------------------------------------



# ---------------------------------------------------------
# CREATE USER VIEW — Buat akun karyawan baru (Owner/Manager only)
# ---------------------------------------------------------
class CreateUserView(APIView):
    permission_classes = [IsOwnerOrManager]

    def post(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()
        role     = request.data.get('role', 'staff')
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
            from ..models import Divisi as DivisiModel
            try:
                user.divisi = DivisiModel.objects.get(pk=divisi)
            except DivisiModel.DoesNotExist:
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


# ---------------------------------------------------------
# FORWARD JOB VIEW — Teruskan job ke tahap/divisi selanjutnya
# ---------------------------------------------------------



# ---------------------------------------------------------



# ---------------------------------------------------------
# STAFF PERFORMANCE REPORT VIEW — Agregasi kinerja karyawan
# ---------------------------------------------------------
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


class HealthCheckView(APIView):
    """GET /api/health/ — Cek status semua komponen sistem."""
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def get(self, request):
        from django.db import connection
        
        status_db = False
        try:
            connection.ensure_connection()
            status_db = True
        except Exception as e:
            logger.error(f"Health check: DB connection error: {e}")

        status_cache = False
        try:
            cache.set('health_check', 'ok', 5)
            status_cache = cache.get('health_check') == 'ok'
        except Exception as e:
            logger.error(f"Health check: Cache access error: {e}")

        overall = status_db and status_cache
        return Response({
            'status': 'ok' if overall else 'degraded',
            'database': 'ok' if status_db else 'error',
            'cache': 'ok' if status_cache else 'error',
            'timestamp': timezone.now().isoformat(),
        }, status=200 if overall else 503)


# ---------------------------------------------------------
# KOMPLAIN VIEWSET — CRUD + Resolve Action
# ---------------------------------------------------------
class KomplainViewSet(viewsets.ModelViewSet):
    """
    GET    /api/komplain/            — Semua komplain (owner/manager: semua; staff/admin: hanya yang dicatat sendiri)
    POST   /api/komplain/            — Buat komplain baru (semua role)
    PATCH  /api/komplain/{id}/       — Update status/resolusi (owner/manager)
    POST   /api/komplain/{id}/resolve/ — Tutup komplain dengan resolusi (owner/manager)
    """
    serializer_class = KomplainOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = KomplainOrder.objects.select_related(
            'order', 'dicatat_oleh', 'ditangani_oleh'
        ).prefetch_related('logs')
        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        # Filter by order_id if provided
        order_id = self.request.query_params.get('order')
        if order_id:
            qs = qs.filter(order_id=order_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(dicatat_oleh=self.request.user)

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        """Manager menyelesaikan komplain dengan menetapkan resolusi."""
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Hanya Owner/Manager yang dapat menyelesaikan komplain.'}, status=403)

        komplain = get_object_or_404(KomplainOrder, pk=pk)
        resolusi = request.data.get('resolusi')
        catatan  = request.data.get('catatan_resolusi', '')
        status_baru = request.data.get('status', 'selesai')

        if not resolusi:
            return Response({'error': 'Field resolusi wajib diisi.'}, status=400)

        with transaction.atomic():
            komplain.resolusi         = resolusi
            komplain.catatan_resolusi = catatan
            komplain.status           = status_baru
            komplain.ditangani_oleh   = request.user
            if status_baru == 'selesai':
                komplain.waktu_selesai = timezone.now()
            komplain.save()

            KomplainLog.objects.create(
                komplain=komplain,
                user=request.user,
                status_baru=status_baru,
                catatan=f"Resolusi: {resolusi}. {catatan}".strip(),
            )

        return Response(KomplainOrderSerializer(komplain).data)

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        """Update status komplain dan tambahkan log entry."""
        komplain = get_object_or_404(KomplainOrder, pk=pk)
        status_baru = request.data.get('status')
        catatan     = request.data.get('catatan', '')

        if not status_baru:
            return Response({'error': 'Field status wajib diisi.'}, status=400)

        valid_statuses = [s[0] for s in KomplainOrder.STATUS_CHOICES]
        if status_baru not in valid_statuses:
            return Response({'error': f'Status tidak valid. Pilihan: {valid_statuses}'}, status=400)

        with transaction.atomic():
            komplain.status = status_baru
            if status_baru == 'selesai':
                komplain.waktu_selesai = timezone.now()
            komplain.save()

            KomplainLog.objects.create(
                komplain=komplain,
                user=request.user,
                status_baru=status_baru,
                catatan=catatan,
            )

        return Response(KomplainOrderSerializer(komplain).data)


# ---------------------------------------------------------
# CRM: CUSTOMER ACTIVITY VIEWSET
# ---------------------------------------------------------
class CustomerActivityViewSet(viewsets.ModelViewSet):
    queryset = CustomerActivity.objects.select_related('order', 'pic').order_by('waktu_jatuh_tempo')
    serializer_class = CustomerActivitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        order_id = self.request.query_params.get('order')
        if order_id:
            qs = qs.filter(order_id=order_id)
        
        selesai_filter = self.request.query_params.get('selesai')
        if selesai_filter == 'true':
            qs = qs.filter(selesai=True)
        elif selesai_filter == 'false':
            qs = qs.filter(selesai=False)

        # Staff hanya melihat task miliknya
        if self.request.user.role == 'staff':
            qs = qs.filter(pic=self.request.user)

        return qs

    def perform_create(self, serializer):
        serializer.save(pic=self.request.user)

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        activity = self.get_object()
        activity.selesai = True
        activity.waktu_selesai = timezone.now()
        activity.save()
        return Response(CustomerActivitySerializer(activity).data)


# ---------------------------------------------------------
# MRP: BILL OF MATERIALS (BOM) & ITEM VIEWSETS
# ---------------------------------------------------------
class BillOfMaterialsViewSet(viewsets.ModelViewSet):
    queryset = BillOfMaterials.objects.select_related('product').prefetch_related('items__inventory_item').all()
    serializer_class = BillOfMaterialsSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        queryset = self.queryset
        product_name = self.request.query_params.get('product_name')
        if product_name:
            queryset = queryset.filter(product__nama_produk=product_name)
        material = self.request.query_params.get('material')
        if material is not None:
            if material == '' or material.lower() == 'null':
                queryset = queryset.filter(product__material__isnull=True) | queryset.filter(product__material='')
            else:
                queryset = queryset.filter(product__material=material)
        return queryset

    @action(detail=False, methods=['post'], url_path='get-or-create-for-product')
    def get_or_create_for_product(self, request):
        product_name = request.data.get('product_name')
        if not product_name:
            return Response({'error': 'product_name wajib diisi'}, status=status.HTTP_400_BAD_REQUEST)
        product_name = product_name.strip()
        
        material = request.data.get('material')
        if material:
            material = material.strip()
            if material == '0' or material.lower() == 'null':
                material = None
        else:
            material = None
            
        with transaction.atomic():
            # Find or create ProductPrice
            product_price_obj = ProductPrice.objects.filter(nama_produk=product_name, material=material).first()
            if not product_price_obj:
                if not material:
                    product_price_obj = ProductPrice.objects.filter(nama_produk=product_name).first()
                if not product_price_obj:
                    product_price_obj = ProductPrice.objects.create(
                        kategori="Umum",
                        nama_produk=product_name,
                        material=material,
                        harga=0
                    )
            
            # Find or create BillOfMaterials
            bom_obj, created = BillOfMaterials.objects.get_or_create(
                product=product_price_obj,
                defaults={'nama': f"BoM {product_price_obj.nama_produk}" + (f" - {product_price_obj.material}" if product_price_obj.material else "")}
            )
            
        serializer = self.get_serializer(bom_obj)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BoMItemViewSet(viewsets.ModelViewSet):
    queryset = BoMItem.objects.select_related('bom', 'inventory_item').all()
    serializer_class = BoMItemSerializer
    permission_classes = [IsOwnerOrManager]





class ClientLogView(APIView):
    """
    POST /api/log-client-error/
    Logs frontend/client-side uncaught crashes to backend log files.
    """
    permission_classes = [] # Allow anonymous logging if crash occurs before login config is fully resolved
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        # Basic pre-shared header token auth check (fail-closed jika tidak dikonfigurasi)
        expected_secret = os.getenv("CLIENT_LOG_SECRET")
        if not expected_secret:
            logger.error("CLIENT_LOG_SECRET is not configured. Client logging is disabled.")
            return Response({"error": "Client log secret not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        auth_header = request.headers.get("X-Client-Log-Auth")
        if not auth_header or auth_header != expected_secret:
            logger.warning(f"Unauthorized call to ClientLogView from IP: {request.META.get('REMOTE_ADDR')}")
            return Response({"error": "Unauthorized"}, status=401)

        error_msg = str(request.data.get('error', 'Unknown Error'))[:500]
        # Remove any newlines or Carriage Returns to prevent log injection
        error_msg = error_msg.replace('\n', ' ').replace('\r', ' ')
        
        error_info = request.data.get('info', {})
        if not isinstance(error_info, dict):
            error_info = {}
            
        url = str(request.data.get('url', 'Unknown URL'))[:200]
        url = url.replace('\n', ' ').replace('\r', ' ')
        
        user_agent = str(request.META.get('HTTP_USER_AGENT', 'Unknown User Agent'))[:200]
        user_agent = user_agent.replace('\n', ' ').replace('\r', ' ')
        
        comp_stack = str(error_info.get('componentStack', 'N/A'))[:2000]
        # We can keep stack trace newlines but escape them or log them safely
        comp_stack = comp_stack.replace('\r', '')
        
        logger.critical(
            f"[CLIENT_CRASH] Uncaught frontend exception:\n"
            f"URL: {url}\n"
            f"Error: {error_msg}\n"
            f"Component Stack: {comp_stack}\n"
            f"User Agent: {user_agent}"
        )
        return Response({"status": "error_logged"}, status=200)


class PublicOrderDetailsView(APIView):
    """
    POST /api/public/get-order-details/
    Get order status & items for public upload design page (AllowAny).
    """
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        order_id = str(request.data.get('order_id', '')).strip().upper()
        nomor_wa = str(request.data.get('nomor_wa', '')).strip()

        if not order_id or not nomor_wa:
            return Response({'error': 'ID Pesanan dan Nomor WhatsApp wajib diisi.'}, status=400)

        cleaned_input = ''.join(filter(str.isdigit, nomor_wa))
        if not cleaned_input:
            return Response({'error': 'Format Nomor WhatsApp tidak valid.'}, status=400)

        # Cari order
        try:
            order = Order.objects.get(id__iexact=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'ID Pesanan tidak ditemukan.'}, status=404)

        cleaned_db = ''.join(filter(str.isdigit, order.nomor_wa))
        
        # Validasi nomor WA (terima kecocokan 9 digit terakhir)
        if cleaned_input[-9:] != cleaned_db[-9:]:
            return Response({'error': 'Nomor WhatsApp tidak cocok dengan data pesanan.'}, status=403)

        items_data = []
        for item in order.items.all():
            items_data.append({
                'id': item.id,
                'jenis_produk': item.jenis_produk,
                'bahan': item.bahan or '-',
                'qty': item.qty,
                'gdrive_customer_link': item.gdrive_customer_link or '',
                'desain_susulan': item.desain_susulan
            })

        return Response({
            'order_id': order.id,
            'nama': order.nama,
            'status_global': order.status_global,
            'items': items_data
        }, status=200)


class PublicSubmitDesignView(APIView):
    """
    POST /api/public/submit-design/
    Submit design url/file for specific order item (AllowAny).
    """
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        order_id = str(request.data.get('order_id', '')).strip().upper()
        nomor_wa = str(request.data.get('nomor_wa', '')).strip()
        item_id = request.data.get('item_id')
        gdrive_link = str(request.data.get('gdrive_link', '')).strip()
        file_obj = request.FILES.get('file')

        if not order_id or not nomor_wa or not item_id:
            return Response({'error': 'ID Pesanan, Nomor WhatsApp, dan Item wajib ditentukan.'}, status=400)

        if not gdrive_link and not file_obj:
            return Response({'error': 'Sertakan Link Google Drive atau unggah File Desain.'}, status=400)

        cleaned_input = ''.join(filter(str.isdigit, nomor_wa))
        try:
            order = Order.objects.get(id__iexact=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'ID Pesanan tidak ditemukan.'}, status=404)

        cleaned_db = ''.join(filter(str.isdigit, order.nomor_wa))
        if cleaned_input[-9:] != cleaned_db[-9:]:
            return Response({'error': 'Nomor WhatsApp tidak cocok.'}, status=403)

        try:
            item = order.items.get(id=item_id)
        except OrderItem.DoesNotExist:
            return Response({'error': 'Item tidak ditemukan pada pesanan ini.'}, status=404)

        if file_obj:
            from django.core.files.storage import default_storage
            import uuid
            # Buat nama file unik agar tidak timpa
            ext = file_obj.name.split('.')[-1]
            unique_name = f"{order_id}_{item.id}_{uuid.uuid4().hex[:6]}.{ext}"
            path = default_storage.save(f'desain_susulan/{unique_name}', file_obj)
            file_url = request.build_absolute_uri(settings.MEDIA_URL + path)
            item.gdrive_customer_link = file_url
        else:
            item.gdrive_customer_link = gdrive_link

        item.desain_susulan = True
        item.save()

        # Log aktifitas
        OrderActivityLog.objects.create(
            order=order,
            user=None,
            tindakan="SUBMIT_DESIGN_SUSULAN",
            keterangan=f"Pelanggan mengunggah file desain susulan untuk item '{item.jenis_produk}'"
        )

        return Response({
            'status': 'success',
            'message': 'Desain susulan berhasil dikirim! Tim kami akan segera meninjau file Anda.',
            'gdrive_customer_link': item.gdrive_customer_link
        }, status=200)


class POSAntrianDeviceViewSet(viewsets.ModelViewSet):
    queryset = POSAntrianDevice.objects.all().order_by('id')
    serializer_class = POSAntrianDeviceSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]


class SaldoKasHarianViewSet(viewsets.ModelViewSet):
    queryset = SaldoKasHarian.objects.all().order_by('-tanggal', '-id')
    serializer_class = SaldoKasHarianSerializer
    permission_classes = [IsAuthenticated, IsOwnerManagerAdminOrKasir]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.role == 'kasir':
            queryset = queryset.filter(kasir=self.request.user)
        tanggal = self.request.query_params.get('tanggal')
        if tanggal:
            queryset = queryset.filter(tanggal=tanggal)
        
        query = self.request.query_params.get('query')
        if query:
            queryset = queryset.filter(
                Q(kasir__username__icontains=query) |
                Q(kasir__first_name__icontains=query) |
                Q(kasir__last_name__icontains=query) |
                Q(shift__icontains=query)
            )
        return queryset


class RingkasanShiftViewSet(viewsets.ModelViewSet):
    queryset = RingkasanShift.objects.all().order_by('-tanggal', '-mulai')
    serializer_class = RingkasanShiftSerializer
    permission_classes = [IsAuthenticated, IsOwnerManagerAdminOrKasir]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.role == 'kasir':
            queryset = queryset.filter(kasir=self.request.user)
        
        tanggal_mulai = self.request.query_params.get('tanggal_mulai')
        tanggal_akhir = self.request.query_params.get('tanggal_akhir')
        
        if tanggal_mulai:
            queryset = queryset.filter(tanggal__gte=tanggal_mulai)
        if tanggal_akhir:
            queryset = queryset.filter(tanggal__lte=tanggal_akhir)
            
        query = self.request.query_params.get('query')
        if query:
            queryset = queryset.filter(
                Q(kasir__username__icontains=query) |
                Q(kasir__first_name__icontains=query) |
                Q(kasir__last_name__icontains=query)
            )
        return queryset