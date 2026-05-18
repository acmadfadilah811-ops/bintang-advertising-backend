import openpyxl
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Order, InventoryItem, JobBoard, Contact
from django.utils import timezone

class ExportContactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="pelanggan_{timezone.now().strftime("%Y%m%d")}.xlsx"'

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Data Pelanggan"

        headers = ['Nama', 'No. WhatsApp', 'Total Order', 'Total Belanja (Rp)', 'Terakhir Order', 'Keterangan']
        ws.append(headers)

        contacts = Contact.objects.all().order_by('-total_spent')
        for c in contacts:
            ws.append([
                c.nama,
                c.nomor_wa,
                c.total_order,
                c.total_spent,
                c.last_order if c.last_order != '-' else '',
                c.keterangan or '',
            ])

        wb.save(response)
        return response


class ExportOrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="orders_{timezone.now().strftime("%Y%m%d")}.xlsx"'
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Orders"
        
        headers = ['ID Pesanan', 'Tanggal', 'Nama Pelanggan', 'No WA', 'Status', 'Total Harga', 'Catatan']
        ws.append(headers)
        
        orders = Order.objects.all().order_by('-waktu')
        for order in orders:
            total_harga = sum([item.harga_jual for item in order.items.all()])
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
        return response

class ExportInventoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="inventory_{timezone.now().strftime("%Y%m%d")}.xlsx"'
        
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
        return response

class ExportJobsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Akses ditolak. Khusus Boss dan Manager.'}, status=403)

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="jobs_{timezone.now().strftime("%Y%m%d")}.xlsx"'
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Jobs"
        
        headers = ['ID Job', 'ID Order', 'Jenis Produk', 'Tahap', 'Divisi', 'Staff PIC', 'Status Pekerjaan', 'Waktu Mulai', 'Waktu Selesai', 'Insentif']
        ws.append(headers)
        
        jobs = JobBoard.objects.all().order_by('-id')
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
        return response
