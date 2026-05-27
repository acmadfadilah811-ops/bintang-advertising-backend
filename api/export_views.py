import openpyxl
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Order, InventoryItem, JobBoard, Contact
from django.utils import timezone

# FIX: Konstanta role agar tidak ada typo string hardcoded di setiap view
ALLOWED_ROLES = ('owner', 'manager')


def _check_role(user):
    """Kembalikan True jika user punya akses export."""
    return user.role in ALLOWED_ROLES


class ExportContactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="pelanggan_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Data Pelanggan"

            headers = ['Nama', 'No. WhatsApp', 'Total Order', 'Total Belanja (Rp)', 'Terakhir Order', 'Keterangan']
            ws.append(headers)

            contacts = Contact.objects.all().order_by('-total_spent')
            for c in contacts:
                # FIX: last_order sekarang DateField, format langsung atau kosong jika None
                # Aman karena DB sudah di-clean oleh migrasi, fallback ke str() jika masih string
                last_order_str = c.last_order.strftime('%Y-%m-%d') if hasattr(c.last_order, 'strftime') else str(c.last_order) if c.last_order else ''
                ws.append([
                    c.nama,
                    c.nomor_wa,
                    c.total_order,
                    c.total_spent,
                    last_order_str,
                    c.keterangan or '',
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response


class ExportOrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="orders_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Orders"

            headers = ['ID Pesanan', 'Tanggal', 'Nama Pelanggan', 'No WA', 'Status', 'Total Harga', 'Catatan']
            ws.append(headers)

            # FIX: Tambahkan prefetch_related('items') untuk menghindari N+1 query
            orders = Order.objects.prefetch_related('items').order_by('-waktu')
            for order in orders:
                total_harga = sum(item.harga_jual for item in order.items.all())
                ws.append([
                    order.id,
                    order.waktu.strftime('%Y-%m-%d %H:%M'),
                    order.nama,
                    order.nomor_wa,
                    order.get_status_global_display(),
                    total_harga,
                    order.catatan_pelanggan or ''
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response


class ExportInventoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="inventory_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Inventory"

            headers = ['ID Item', 'Nama', 'Kategori', 'Stok Saat Ini', 'Satuan', 'Min Stok', 'Harga Beli/Unit', 'Supplier', 'Status']
            ws.append(headers)

            items = InventoryItem.objects.all().order_by('nama')
            for item in items:
                status = 'Kritis' if item.stok < item.min_stok else 'Aman'
                ws.append([
                    item.id,
                    item.nama,
                    item.kategori,
                    item.stok,
                    item.satuan,
                    item.min_stok,
                    item.cost_per_unit,
                    item.supplier,
                    status
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response


class ExportJobsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="jobs_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Jobs"

            headers = ['ID Job', 'ID Order', 'Jenis Produk', 'Tahap', 'Divisi', 'Staff PIC', 'Status Pekerjaan', 'Waktu Mulai', 'Waktu Selesai', 'Insentif']
            ws.append(headers)

            # FIX: select_related untuk semua relasi yang diakses dalam loop → eliminasi N+1 query berganda
            jobs = JobBoard.objects.select_related(
                'order_item__order',
                'tahap__divisi',
                'pic_staff'
            ).order_by('-id')

            for job in jobs:
                ws.append([
                    job.id,
                    job.order_item.order.id if job.order_item and job.order_item.order else '',
                    job.order_item.jenis_produk if job.order_item else '',
                    job.tahap.nama if job.tahap else 'Tanpa Tahap',
                    job.tahap.divisi.nama if job.tahap and job.tahap.divisi else 'Tanpa Divisi',
                    job.pic_staff.username if job.pic_staff else 'Belum diset',
                    job.get_status_pekerjaan_display(),
                    job.waktu_mulai.strftime('%Y-%m-%d %H:%M') if job.waktu_mulai else '-',
                    job.waktu_selesai.strftime('%Y-%m-%d %H:%M') if job.waktu_selesai else '-',
                    job.insentif
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response

class ExportAbsensiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        tanggal = request.query_params.get("tanggal")
        if not tanggal:
            tanggal = timezone.localdate().strftime("%Y-%m-%d")

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="absensi_{tanggal}.xlsx"'

        try:
            from hr.models import Absensi
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Absensi {tanggal}"

            headers = ['Nama Staff', 'Divisi', 'Status Kehadiran', 'Waktu Masuk', 'Waktu Keluar', 'Durasi Kerja (Jam)', 'Keterangan']
            ws.append(headers)

            absensi = Absensi.objects.filter(tanggal=tanggal).select_related('staff', 'staff__divisi').order_by('staff__username')

            for a in absensi:
                masuk = a.jam_masuk.strftime('%H:%M:%S') if a.jam_masuk else '-'
                keluar = a.jam_keluar.strftime('%H:%M:%S') if a.jam_keluar else '-'
                ws.append([
                    a.staff.get_full_name() or a.staff.username,
                    a.staff.divisi.nama if a.staff.divisi else 'Belum Ditentukan',
                    a.get_status_display(),
                    masuk,
                    keluar,
                    a.durasi_kerja_jam,
                    a.catatan or ''
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response


class ExportStaffPerformanceView(APIView):
    """
    GET /api/export/staff-performance/?range=bulan_ini
    Mengekspor laporan kinerja seluruh staff ke dalam format Excel (.xlsx)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        time_range = request.query_params.get('range', 'bulan_ini')
        
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="kinerja_karyawan_{time_range}_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Kinerja Karyawan"

            headers = [
                'Nama Karyawan', 'Username', 'Divisi', 
                'Total Tugas', 'Tugas Selesai', 'Sedang Dikerjakan', 
                'Antrean', 'Gagal/Batal', 'Ada Kendala', 
                'Total Insentif (Rp)', 'Rata-rata Durasi (Menit)'
            ]
            ws.append(headers)

            import calendar
            from api.models import CustomUser
            
            now = timezone.localtime(timezone.now())
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

            staff_members = CustomUser.objects.filter(role='staff').select_related('divisi').prefetch_related('my_tasks')

            for staff in staff_members:
                jobs = staff.my_tasks.all()
                if start and end:
                    completed_jobs = jobs.filter(status_pekerjaan='selesai', waktu_selesai__gte=start, waktu_selesai__lte=end)
                    failed_jobs = jobs.filter(status_pekerjaan__in=['gagal', 'batal'], waktu_selesai__gte=start, waktu_selesai__lte=end)
                else:
                    completed_jobs = jobs.filter(status_pekerjaan='selesai')
                    failed_jobs = jobs.filter(status_pekerjaan__in=['gagal', 'batal'])
                    
                active_jobs = jobs.filter(status_pekerjaan='dikerjakan')
                pending_jobs = jobs.filter(status_pekerjaan='antrean')
                constraint_jobs = jobs.filter(status_pekerjaan='kendala')

                total_jobs = completed_jobs.count() + failed_jobs.count() + active_jobs.count() + pending_jobs.count() + constraint_jobs.count()
                total_insentif = sum(j.insentif for j in completed_jobs)

                durations = []
                for j in completed_jobs:
                    if j.waktu_mulai and j.waktu_selesai:
                        diff = j.waktu_selesai - j.waktu_mulai
                        durations.append(diff.total_seconds() / 60.0)
                
                avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

                ws.append([
                    staff.get_full_name() or staff.username,
                    staff.username,
                    staff.divisi.nama if staff.divisi else '-',
                    total_jobs,
                    completed_jobs.count(),
                    active_jobs.count(),
                    pending_jobs.count(),
                    failed_jobs.count(),
                    constraint_jobs.count(),
                    total_insentif,
                    avg_duration
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response