import openpyxl
import logging
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from api.permissions import IsOwnerOrManager
from rest_framework.response import Response
from .models import Order, InventoryItem, JobBoard, Contact
from .customer_models import Customer
from django.utils import timezone

logger = logging.getLogger(__name__)

class ExportContactsView(APIView):
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
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


class ExportCustomersView(APIView):
    """GET /api/export/customers/ — ekspor data Customer (Pelanggan & Supplier > Pelanggan)."""
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="daftar_pelanggan_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Daftar Pelanggan"

            headers = [
                'Kode Pelanggan', 'Nama', 'Tipe Pelanggan', 'Handphone', 'Email',
                'Jenis Kelamin', 'Tanggal Lahir', 'Nama Perusahaan', 'Batas Kredit',
                'Deposit', 'Loyalty Points', 'Terima Buletin', 'Status',
                'Tanggal Berakhir', 'Alamat', 'Kota', 'Kecamatan', 'Kode Pos', 'Catatan',
            ]
            ws.append(headers)

            customers = Customer.objects.select_related('customer_group').order_by('-created_at')
            for c in customers:
                ws.append([
                    c.kode_pelanggan,
                    c.nama,
                    c.customer_group.nama if c.customer_group else '-',
                    c.handphone,
                    c.email,
                    c.get_jenis_kelamin_display() if c.jenis_kelamin else '-',
                    c.tanggal_lahir.strftime('%Y-%m-%d') if c.tanggal_lahir else '-',
                    c.nama_perusahaan or '-',
                    float(c.batas_kredit),
                    float(c.deposit),
                    c.loyalty_points,
                    'Ya' if c.terima_buletin else 'Tidak',
                    'Dibekukan' if c.bekukan else 'Aktif',
                    c.tanggal_berakhir.strftime('%Y-%m-%d') if c.tanggal_berakhir else '-',
                    c.alamat,
                    c.kota,
                    c.kecamatan,
                    c.kode_pos,
                    c.catatan,
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response


class ExportOrdersView(APIView):
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
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
            except Exception as e:
                logger.warning(f"Gagal mem-parse start_date '{start_date_str}': {e}")
        if end_date_str:
            try:
                from django.utils.dateparse import parse_date
                e_date = parse_date(end_date_str)
                if e_date:
                    orders_qs = orders_qs.filter(waktu__date__lte=e_date)
            except Exception as e:
                logger.warning(f"Gagal mem-parse end_date '{end_date_str}': {e}")

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
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
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
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
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
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
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
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
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


class ExportStockMovementView(APIView):
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        import datetime
        from django.utils.dateparse import parse_date

        if start_date_str:
            start_date = parse_date(start_date_str)
        else:
            start_date = datetime.date.today()

        if end_date_str:
            end_date = parse_date(end_date_str)
        else:
            end_date = datetime.date.today()

        filename = f"summary-{start_date.strftime('%Y-%m-%d')}__{end_date.strftime('%Y-%m-%d')}.xlsx"

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Pergerakan Stok"

            headers = ['Grup', 'Produk', 'Awal', 'Masuk', 'Pengembalian', 'Penjualan', 'Keluar', 'Sisa']
            ws.append(headers)

            from .product_models import Product, ProductVariant, ProductStockMovement

            products = Product.objects.all().select_related('kategori').prefetch_related('variants')

            skus = {}
            for p in products:
                if p.has_variant and p.variants.exists():
                    for v in p.variants.all():
                        skus[(p.id, v.id)] = {
                            'product_id': p.id,
                            'variant_id': v.id,
                            'product_name': p.nama,
                            'variant_name': v.nama_varian,
                            'sku': v.sku or p.sku or '',
                            'group': p.kategori.nama if p.kategori else 'Umum',
                            'current_qty': v.qty_stok,
                        }
                else:
                    skus[(p.id, None)] = {
                        'product_id': p.id,
                        'variant_id': None,
                        'product_name': p.nama,
                        'variant_name': '',
                        'sku': p.sku or '',
                        'group': p.kategori.nama if p.kategori else 'Umum',
                        'current_qty': p.qty_stok,
                    }

            movements = ProductStockMovement.objects.all().order_by('created_at')

            summary_map = {}
            for key, info in skus.items():
                summary_map[key] = {
                    'group': info['group'],
                    'product': f"{info['product_name']} - {info['variant_name']}" if info['variant_name'] else info['product_name'],
                    'initial': float(info['current_qty']),
                    'in': 0.0,
                    'returnStock': 0.0,
                    'sales': 0.0,
                    'out': 0.0,
                    'sisa': float(info['current_qty']),
                    'movements_before': [],
                    'movements_during': [],
                }

            for m in movements:
                m_date = m.tanggal or m.created_at.date()
                key = (m.product_id, m.variant_id)
                if key not in summary_map:
                    continue

                if m_date < start_date:
                    summary_map[key]['movements_before'].append(m)
                elif start_date <= m_date <= end_date:
                    summary_map[key]['movements_during'].append(m)

            for key, s in summary_map.items():
                info = skus[key]

                if s['movements_before']:
                    last_before = s['movements_before'][-1]
                    initial_val = last_before.stok_akhir
                elif s['movements_during']:
                    first_during = s['movements_during'][0]
                    initial_val = first_during.stok_awal
                else:
                    movements_after = ProductStockMovement.objects.filter(
                        product_id=info['product_id'],
                        variant_id=info['variant_id']
                    ).order_by('created_at')
                    after_list = []
                    for m in movements_after:
                        m_date = m.tanggal or m.created_at.date()
                        if m_date > end_date:
                            after_list.append(m)
                    if after_list:
                        initial_val = after_list[0].stok_awal
                    else:
                        initial_val = info['current_qty']

                s['initial'] = float(initial_val)

                in_qty = 0.0
                out_qty = 0.0
                sales_qty = 0.0
                return_qty = 0.0

                for m in s['movements_during']:
                    qty = float(m.qty)
                    if m.tipe in ('masuk', 'produksi'):
                        in_qty += qty
                    elif m.tipe == 'keluar':
                        out_qty += qty
                    elif m.tipe == 'penjualan':
                        sales_qty += qty
                    elif m.tipe == 'pengembalian':
                        return_qty += qty

                s['in'] = in_qty
                s['out'] = out_qty
                s['sales'] = sales_qty
                s['returnStock'] = return_qty

                if s['movements_during']:
                    sisa_val = s['movements_during'][-1].stok_akhir
                elif s['movements_before']:
                    sisa_val = s['movements_before'][-1].stok_akhir
                else:
                    sisa_val = initial_val

                s['sisa'] = float(sisa_val)

                ws.append([
                    s['group'],
                    s['product'],
                    s['initial'],
                    s['in'],
                    s['returnStock'],
                    s['sales'],
                    s['out'],
                    s['sisa'],
                ])

            wb.save(response)
        except Exception as e:
            return Response({'error': f'Gagal membuat file Excel: {str(e)}'}, status=500)

        return response


class ExportProductsView(APIView):
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        import csv
        from django.http import HttpResponse
        from .product_models import Product

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="products_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        # Ensure correct UTF-8 BOM encoding for Excel compatibility
        response.write(u'\ufeff'.encode('utf8'))
        
        writer = csv.writer(response)
        
        # Headers
        headers = [
            "name", "alternative_name", "classification_id", "category",
            "variant_column_labels", "variant_names", "alternative_variant_name",
            "variant_view_order", "collections", "brand", "condition_id", "uom",
            "sku", "barcode", "serial_nos", "description", "buy_price",
            "sell_price", "pos_sell_price", "market_price", "wholesale_group",
            "wholesale_price", "pos_sell_price_dynamic", "track_inventory",
            "stock_qty", "low_stock_alert", "is_out_stock", "weight_kg",
            "loyalty_points", "comission", "published", "hidden_in_pos",
            "qty_fast_moving", "rack", "is_always_available", "non_taxable",
            "customer_comission", "is_customer_comission_percentage", "non_service_charge"
        ]
        writer.writerow(headers)

        products = Product.objects.select_related('kategori', 'brand', 'koleksi').prefetch_related('variants').all()
        
        for p in products:
            category_name = p.kategori.nama if p.kategori else ""
            brand_name = p.brand.nama if p.brand else ""
            collection_name = p.koleksi.nama if p.koleksi else ""
            
            if p.has_variant and p.variants.exists():
                for idx, v in enumerate(p.variants.all()):
                    writer.writerow([
                        p.nama,
                        p.nama_alternatif or "",
                        "",  # classification_id
                        category_name,
                        "Varian",  # variant_column_labels
                        v.nama_varian,
                        v.nama_alternatif or "",
                        str(idx + 1),  # variant_view_order
                        collection_name,
                        brand_name,
                        "N",  # condition_id
                        p.satuan or "pcs",
                        v.sku or "",
                        v.barcode or "",
                        "",  # serial_nos
                        p.deskripsi or "",
                        str(v.harga_beli),
                        str(v.harga_jual_online),
                        str(v.harga_jual_toko),
                        str(v.harga_pasar),
                        "",  # wholesale_group
                        "0",  # wholesale_price
                        "0",  # pos_sell_price_dynamic
                        "1" if v.lacak_inventori else "0",
                        str(v.qty_stok),
                        str(p.stok_minimum),
                        "0" if v.qty_stok > 0 else "1",
                        str(float(v.berat or 0.0) / 1000.0),  # weight_kg
                        "0",  # loyalty_points
                        "0",  # commission
                        "1" if p.is_active else "0",
                        "0",  # hidden_in_pos
                        str(p.qty_fast_moving),
                        v.rack or p.rack or "",
                        "1" if not v.lacak_inventori else "0",
                        "0",  # non_taxable
                        "0",  # customer_comission
                        "0",  # is_customer_comission_percentage
                        "0"   # non_service_charge
                    ])
            else:
                writer.writerow([
                    p.nama,
                    p.nama_alternatif or "",
                    "",  # classification_id
                    category_name,
                    "",  # variant_column_labels
                    "",  # variant_names
                    "",  # alternative_variant_name
                    "",  # variant_view_order
                    collection_name,
                    brand_name,
                    "N",  # condition_id
                    p.satuan or "pcs",
                    p.sku or "",
                    p.barcode or "",
                    "",  # serial_nos
                    p.deskripsi or "",
                    str(p.harga_beli),
                    str(p.harga_jual_online),
                    str(p.harga_jual_toko),
                    "0",  # market_price
                    "",  # wholesale_group
                    "0",  # wholesale_price
                    "0",  # pos_sell_price_dynamic
                    "1" if p.lacak_inventori else "0",
                    str(p.qty_stok),
                    str(p.stok_minimum),
                    "0" if p.qty_stok > 0 else "1",
                    "0",  # weight_kg
                    "0",  # loyalty_points
                    "0",  # commission
                    "1" if p.is_active else "0",
                    "0",  # hidden_in_pos
                    str(p.qty_fast_moving),
                    p.rack or "",
                    "1" if not p.lacak_inventori else "0",
                    "0",  # non_taxable
                    "0",  # customer_comission
                    "0",  # is_customer_comission_percentage
                    "0"   # non_service_charge
                ])
                
        return response