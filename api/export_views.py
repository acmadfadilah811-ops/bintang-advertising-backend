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

            headers = ['Nama', 'No. WhatsApp', 'Total Order', 'Total Belanja (Rp)', 'Terakhir Order', 'Produk Dipesan', 'Keterangan']
            ws.append(headers)

            contacts = Contact.objects.all().order_by('-total_spent')
            
            # Optimasi N+1: Prefetch seluruh order sekaligus dan kelompokkan menggunakan dictionary
            from collections import defaultdict
            orders_by_wa = defaultdict(list)
            all_orders = Order.objects.prefetch_related('items').all().order_by('-waktu')
            for order in all_orders:
                orders_by_wa[order.nomor_wa].append(order)

            for c in contacts:
                # Ambil daftar produk yang dipesan beserta tanggalnya
                ordered_products_with_dates = []
                customer_orders = orders_by_wa.get(c.nomor_wa, [])
                for order in customer_orders:
                    tgl_str = order.waktu.strftime('%d/%m/%Y')
                    for item in order.items.all():
                        ordered_products_with_dates.append(f"{item.jenis_produk} ({tgl_str})")
                products_str = ", ".join(ordered_products_with_dates) if ordered_products_with_dates else "-"

                last_order_str = c.last_order.strftime('%Y-%m-%d') if hasattr(c.last_order, 'strftime') else str(c.last_order) if c.last_order else ''
                ws.append([
                    c.nama,
                    c.nomor_wa,
                    c.total_order,
                    c.total_spent,
                    last_order_str,
                    products_str,
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

        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        # Base Query
        orders_qs = Order.objects.prefetch_related(
            'items', 'items__jobs', 'items__jobs__pic_staff'
        ).order_by('-waktu')

        if start_date_str:
            try:
                from django.utils.dateparse import parse_date
                s_date = parse_date(start_date_str)
                if s_date:
                    orders_qs = orders_qs.filter(waktu__date__gte=s_date)
            except Exception:
                pass
        if end_date_str:
            try:
                from django.utils.dateparse import parse_date
                e_date = parse_date(end_date_str)
                if e_date:
                    orders_qs = orders_qs.filter(waktu__date__lte=e_date)
            except Exception:
                pass

        # Safety limit to prevent memory issue
        total_count = orders_qs.count()
        if total_count > 50000:
            return Response({
                'error': 'Terlalu banyak data untuk diekspor. Gunakan filter rentang tanggal (start_date & end_date) untuk membatasi hasil.',
                'suggestion': f'Total data saat ini: {total_count} (Maksimal: 50.000)'
            }, status=400)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="orders_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Orders"

            headers = [
                'ID Pesanan', 'Tanggal', 'Nama Pelanggan', 'No WA', 
                'Produk yang Dipesan', 'Jumlah', 'PIC / Order Service', 
                'Status', 'Total Harga', 'Catatan'
            ]
            ws.append(headers)

            for order in orders_qs:
                items = order.items.all()
                products = ", ".join([it.jenis_produk for it in items])
                quantities = ", ".join([str(it.qty) for it in items])
                
                pics = set()
                for it in items:
                    for job in it.jobs.all():
                        if job.pic_staff:
                            pics.add(job.pic_staff.username)
                pic_str = ", ".join(pics) if pics else "-"

                total_harga = sum(item.harga_jual for item in items)
                ws.append([
                    order.id,
                    order.waktu.strftime('%Y-%m-%d %H:%M'),
                    order.nama,
                    order.nomor_wa,
                    products,
                    quantities,
                    pic_str,
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

            headers = ['ID Item', 'Nama', 'Kategori', 'Stok Saat Ini', 'Satuan', 'Min Stok', 'Harga Beli/Unit', 'Supplier', 'Tanggal Restok Terakhir', 'Tanggal Update/Mutasi', 'Status']
            ws.append(headers)

            items = InventoryItem.objects.prefetch_related('history').order_by('nama')
            for item in items:
                status = 'Kritis' if item.stok < item.min_stok else 'Aman'
                
                # Evaluasi di memory Python agar tidak memicu N+1 query.
                # Karena RestockHistory memiliki Meta ordering = ['-waktu'], list ini sudah berurutan terbalik secara default.
                history_list = list(item.history.all())
                
                latest_history = history_list[0] if history_list else None
                tanggal_update_str = latest_history.waktu.strftime('%Y-%m-%d %H:%M') if latest_history else '-'
                
                restock_list = [h for h in history_list if h.delta > 0]
                latest_restock = restock_list[0] if restock_list else None
                tanggal_restok_str = latest_restock.waktu.strftime('%Y-%m-%d %H:%M') if latest_restock else '-'

                ws.append([
                    item.id,
                    item.nama,
                    item.kategori,
                    item.stok,
                    item.satuan,
                    item.min_stok,
                    item.cost_per_unit,
                    item.supplier,
                    tanggal_restok_str,
                    tanggal_update_str,
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
    GET /api/export/staff-performance/?range=bulan_ini&type=global&divisi=...&staff_id=...
    Mengekspor laporan detail kinerja (global/divisi/staff) ke Excel.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _check_role(request.user):
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        time_range = request.query_params.get('range', 'bulan_ini')
        export_type = request.query_params.get('type', 'global')  # 'global', 'divisi', 'staff'
        divisi_name = request.query_params.get('divisi', '')
        staff_id = request.query_params.get('staff_id', '')

        import calendar
        from api.models import JobBoard, CustomUser
        
        now = timezone.localtime(timezone.now())
        start = None
        end = None

        if time_range == 'hari_ini':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif time_range == 'tujuh_hari':
            start = (now - timezone.timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif time_range == 'bulan_ini':
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

        jobs_qs = JobBoard.objects.select_related(
            'order_item', 'order_item__order', 'tahap', 'tahap__divisi', 'pic_staff'
        ).order_by('-waktu_selesai')

        if start and end:
            jobs_qs = jobs_qs.filter(waktu_selesai__gte=start, waktu_selesai__lte=end)
        else:
            jobs_qs = jobs_qs.filter(waktu_selesai__isnull=False)

        title_suffix = "Global"
        filename_prefix = "laporan_global"

        if export_type == 'divisi' and divisi_name:
            jobs_qs = jobs_qs.filter(tahap__divisi__nama__iexact=divisi_name)
            title_suffix = f"Divisi {divisi_name}"
            filename_prefix = f"laporan_divisi_{divisi_name.lower().replace(' ', '_')}"
        elif export_type == 'staff' and staff_id:
            try:
                staff_user = CustomUser.objects.get(id=staff_id)
                jobs_qs = jobs_qs.filter(pic_staff=staff_user)
                title_suffix = f"Staff {staff_user.get_full_name() or staff_user.username}"
                filename_prefix = f"laporan_staff_{staff_user.username}"
            except CustomUser.DoesNotExist:
                return Response({'error': 'Staff tidak ditemukan'}, status=404)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{filename_prefix}_{time_range}_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Detail Pekerjaan"

            # Title block
            ws.append([f"LAPORAN DETAIL PRODUKSI - {title_suffix.upper()}"])
            periode_str = f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}" if start else "Semua Waktu"
            ws.append([f"Periode: {time_range.replace('_', ' ').title()} ({periode_str})"])
            ws.append([]) # Blank spacer

            headers = [
                'No', 'ID Job', 'Tanggal Selesai', 'ID Order', 'Pelanggan', 
                'Nama Produk', 'Bahan Baku', 'Qty', 'Divisi', 'Tahap Proses', 
                'ID PIC', 'Nama PIC', 'Durasi (Menit)', 'Status', 'Insentif (Rp)', 'Biaya Desain (Rp)'
            ]
            ws.append(headers)

            for idx, job in enumerate(jobs_qs, 1):
                duration_minutes = 0
                if job.waktu_mulai and job.waktu_selesai:
                    diff = job.waktu_selesai - job.waktu_mulai
                    duration_minutes = round(diff.total_seconds() / 60.0, 1)

                ws.append([
                    idx,
                    job.id,
                    job.waktu_selesai.strftime('%Y-%m-%d %H:%M') if job.waktu_selesai else '-',
                    job.order_item.order.id if job.order_item and job.order_item.order else '-',
                    job.order_item.order.nama if job.order_item and job.order_item.order else '-',
                    job.order_item.jenis_produk if job.order_item else '-',
                    job.order_item.bahan if job.order_item else '-',
                    job.order_item.qty if job.order_item else 1,
                    job.tahap.divisi.nama if job.tahap and job.tahap.divisi else '-',
                    job.tahap.nama if job.tahap else '-',
                    job.pic_staff.username if job.pic_staff else '-',
                    job.pic_staff.get_full_name() or job.pic_staff.username if job.pic_staff else '-',
                    duration_minutes,
                    job.status_pekerjaan,
                    job.insentif,
                    job.biaya_desain
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response