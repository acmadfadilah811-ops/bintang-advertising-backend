import uuid
import logging
import csv
import io
import datetime
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Count, Sum
from django.http import HttpResponse


from .. import spk
from ..models import (
    Order, OrderItem, JobBoard, CustomUser, Contact, OrderActivityLog, TahapProses
)
from ..serializers import (
    OrderSerializer, OrderItemSerializer, JobBoardSerializer
)
from ..permissions import IsOwnerOrManager, IsOwnerManagerAdminOrKasir, IsClockedIn
from hr.models import Akun, TransaksiBukuBesar
from users.models import SecurityAuditLog

from .jobs import deduct_job_materials_if_needed

logger = logging.getLogger(__name__)


def record_payment_to_general_ledger(order, jumlah_bayar, metode, is_dp=False):
    """
    Mencatat pembayaran (DP / Pelunasan) secara otomatis ke Buku Besar (Double-Entry Bookkeeping).
    """
    try:
        metode_clean = (metode or 'tunai').lower()
        if metode_clean == 'transfer':
            kode_aset = '1-1001'
            nama_aset = 'Bank Transfer'
        elif metode_clean == 'qris':
            kode_aset = '1-1002'
            nama_aset = 'QRIS / E-Wallet'
        else:
            kode_aset = '1-1000'
            nama_aset = 'Kas Tunai'
            
        akun_aset, _ = Akun.objects.get_or_create(
            kode_akun=kode_aset,
            defaults={'nama_akun': nama_aset, 'kategori': 'Aset'}
        )
        akun_pendapatan, _ = Akun.objects.get_or_create(
            kode_akun='4-1000',
            defaults={'nama_akun': 'Pendapatan Jasa Cetak', 'kategori': 'Pendapatan'}
        )
        
        ref_no = f"Order #{order.id}"
        tipe_bayar = "DP" if is_dp else "Pelunasan"
        ket_tx = f"Pembayaran {tipe_bayar} {metode_clean.upper()} - Pelanggan: {order.nama} ({ref_no})"
        
        # DEBIT ke akun Aset (Kas/Bank bertambah)
        TransaksiBukuBesar.objects.create(
            akun=akun_aset,
            tanggal=timezone.localdate(),
            no_referensi=ref_no,
            keterangan=ket_tx,
            debit=jumlah_bayar,
            kredit=0
        )
        
        # KREDIT ke akun Pendapatan (Pendapatan bertambah)
        TransaksiBukuBesar.objects.create(
            akun=akun_pendapatan,
            tanggal=timezone.localdate(),
            no_referensi=ref_no,
            keterangan=ket_tx,
            debit=0,
            kredit=jumlah_bayar
        )
    except Exception as e:
        logger.error(f"Gagal mencatat jurnal Buku Besar otomatis untuk Order #{getattr(order, 'id', '?')}: {e}", exc_info=True)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        base_qs = Order.objects.prefetch_related(
            'items__jobs',
            'items__jobs__tahap',
            'items__jobs__tahap__divisi',
            'items__jobs__pic_staff',
            'items__jobs__pic_staff__divisi',
            # OrderItemSerializer mengekspos product_nama/sku/brand/kategori dan
            # variant_nama. Tanpa prefetch ini tiap item memicu query sendiri.
            'items__product__brand',
            'items__product__kategori',
            'items__variant',
            # OrderSerializer menyertakan activity_logs beserta user tiap log —
            # penyumbang query terbesar bila tidak di-prefetch.
            'activity_logs__user',
        ).order_by('-waktu')
        
        # ✅ Filter by nomor_wa if provided in query params (optimasi query detail customer)
        nomor_wa = self.request.query_params.get('nomor_wa')
        if nomor_wa:
            base_qs = base_qs.filter(nomor_wa=nomor_wa)
            
        # ✅ Filter search di database level
        search = self.request.query_params.get('search')
        if search:
            base_qs = base_qs.filter(
                Q(id__icontains=search) |
                Q(nama__icontains=search) |
                Q(nomor_wa__icontains=search)
            )
            
        # Filter status_global/sumber eksplisit. Sebelumnya hanya `tab` yang
        # dikenali, sehingga permintaan seperti ?status_global=review&sumber=wa
        # (dipakai antrean WA dan badge topbar kasir) diabaikan diam-diam dan
        # mengembalikan SELURUH order — badge pun menghitung semuanya.
        status_global = self.request.query_params.get('status_global')
        if status_global:
            valid_status = {kode for kode, _ in Order.STATUS_GLOBAL_CHOICES}
            if status_global in valid_status:
                base_qs = base_qs.filter(status_global=status_global)

        sumber = self.request.query_params.get('sumber')
        if sumber:
            base_qs = base_qs.filter(sumber=sumber)

        # ✅ Filter tab/status di database level
        tab = self.request.query_params.get('tab')
        if tab:
            tab_map = {
                'pending': 'review',
                'printing': 'proses',
                'completed': 'selesai',
                'cancelled': 'batal'
            }
            mapped_tab = tab_map.get(tab, tab)
            if mapped_tab == 'piutang':
                base_qs = base_qs.filter(sisa_tagihan__gt=0).exclude(status_global='batal')
            elif mapped_tab in ['draft', 'quotation', 'review', 'desain', 'proses', 'ready', 'selesai', 'batal']:
                base_qs = base_qs.filter(status_global=mapped_tab)
            
        # Kasir ikut melihat seluruh order: perannya di meja depan — menerima
        # antrean WA, membuat order, menagih pesanan yang siap diambil. Batasan
        # pic_staff di bawah ditujukan untuk staff produksi, yang memang hanya
        # boleh melihat pekerjaannya sendiri. Tanpa kasir di daftar ini, semua
        # layar kasir yang membaca /orders/ menerima daftar kosong.
        if user.role in ['owner', 'manager', 'admin', 'kasir']:
            return base_qs
        my_order_ids = JobBoard.objects.filter(
            pic_staff=user
        ).values_list('order_item__order_id', flat=True)
        return base_qs.filter(id__in=my_order_ids)

    def perform_create(self, serializer):
        # Auto-generate ID: ORD-20260517-A3F2
        today = timezone.now().strftime('%Y%m%d')
        short_id = uuid.uuid4().hex[:4].upper()
        order_id = f'ORD-{today}-{short_id}'
        instance = serializer.save(id=order_id, _current_user=self.request.user)
        
        # Log pembuatan pesanan
        OrderActivityLog.objects.create(
            order=instance,
            user=self.request.user,
            tindakan="CREATE_ORDER",
            keterangan=f"Pesanan baru '{instance.id}' berhasil dibuat."
        )

        # Buku Besar Otomatis jika ada DP awal
        if instance.dp_dibayar > 0:
            record_payment_to_general_ledger(
                order=instance,
                jumlah_bayar=instance.dp_dibayar,
                metode=getattr(instance, 'metode_pembayaran', 'tunai'),
                is_dp=True
            )

    def perform_update(self, serializer):
        serializer.instance._current_user = self.request.user
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        # Proteksi Keamanan: Hanya Owner dan Manager yang boleh menghapus pesanan
        if request.user.role not in ['owner', 'manager']:
            SecurityAuditLog.objects.create(
                user=request.user,
                event="PERMISSION_DENIED",
                ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                keterangan=f"Ditolak menghapus pesanan {kwargs.get('pk')} karena hak akses tidak mencukupi (role: {request.user.role})",
                berhasil=False,
            )
            return Response({'error': 'Hanya Owner atau Manager yang diperbolehkan menghapus pesanan secara permanen.'}, status=status.HTTP_403_FORBIDDEN)
        
        order = self.get_object()
        # Catat audit log penghapusan sebelum dihapus
        SecurityAuditLog.objects.create(
            user=request.user,
            event="TOKEN_REVOKED", # Menggunakan token_revoked sebagai representasi general admin revoke action
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
            keterangan=f"Berhasil menghapus permanen pesanan '{order.id}' milik pelanggan '{order.nama}'",
            berhasil=True,
        )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/orders/stats/
        Mendapatkan data statistik ringkasan pesanan untuk dashboard pesanan secara cepat dari database.
        """
        user = self.request.user
        base_qs = Order.objects.all()
        
        # Sesuai get_queryset, batasi data jika bukan admin/owner/manager
        if user.role not in ['owner', 'manager', 'admin']:
            my_order_ids = JobBoard.objects.filter(
                pic_staff=user
            ).values_list('order_item__order_id', flat=True)
            base_qs = base_qs.filter(id__in=my_order_ids)
            
        stats_agg = base_qs.aggregate(
            total_count=Count('id'),
            total_piutang=Sum('sisa_tagihan'),
            piutang_count=Count('id', filter=Q(sisa_tagihan__gt=0) & ~Q(status_global='batal'))
        )
        
        # Kelompokkan berdasarkan status_global
        status_counts = base_qs.values('status_global').annotate(count=Count('id'))
        status_map = {item['status_global']: item['count'] for item in status_counts}
        
        # Hitung sisa tagihan khusus yang tidak batal
        piutang_non_batal = base_qs.exclude(status_global='batal').aggregate(
            total=Sum('sisa_tagihan')
        )['total'] or 0
        
        return Response({
            'total_count': stats_agg['total_count'] or 0,
            'total_piutang': piutang_non_batal,
            'piutang_count': stats_agg['piutang_count'] or 0,
            'draft': status_map.get('draft', 0),
            'quotation': status_map.get('quotation', 0),
            'review': status_map.get('review', 0),
            'desain': status_map.get('desain', 0),
            'proses': status_map.get('proses', 0),
            'ready': status_map.get('ready', 0),
            'selesai': status_map.get('selesai', 0),
            'batal': status_map.get('batal', 0),
        })

    @action(detail=True, methods=['get'], url_path='print-return')
    def print_return(self, request, pk=None):
        """
        GET /api/orders/{order_id}/print-return/
        Generate HTML untuk print/download PDF pengembalian pesanan.
        """
        try:
            order = self.get_object()
        except Order.DoesNotExist:
            return Response({'error': 'Order tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        # Parse return info dari catatan_pelanggan
        def get_return_info(catatan):
            if not catatan:
                return None
            import re
            match = re.search(
                r'\[PENGEMBALIAN - Tanggal:\s*([^\s,]+),\s*Status:\s*([^,]*),\s*Catatan:\s*([^\]]*)\]',
                catatan
            ) or re.search(
                r'\[PENGEMBALIAN - Tanggal:\s*([^\s,]+),\s*Catatan:\s*([^\]]*)\]',
                catatan
            )
            if match:
                if len(match.groups()) == 3:
                    return {'tanggal': match.group(1), 'status': match.group(2), 'catatan': match.group(3)}
                return {'tanggal': match.group(1), 'status': 'Tunda', 'catatan': match.group(2)}
            return None

        return_info = get_return_info(order.catatan_pelanggan)
        if not return_info:
            return Response({'error': 'Data pengembalian tidak ditemukan.'}, status=status.HTTP_400_BAD_REQUEST)

        # Ambil nama akun yang membuat order (dari activity_logs)
        creator_name = 'System'
        create_log = order.activity_logs.filter(tindakan='CREATE_ORDER').first()
        if create_log and create_log.user:
            creator_name = create_log.user.username

        # Generate return ID (SR format)
        order_time = order.waktu
        return_id = f"SR{order_time.strftime('%y%m%d')}0000000{order.id}"

        # Generate HTML
        html = self._generate_return_html(
            return_id=return_id,
            order=order,
            return_info=return_info,
            creator_name=creator_name
        )

        response = HttpResponse(html, content_type='text/html')
        return response

    def _generate_return_html(self, return_id, order, return_info, creator_name):
        """Generate HTML untuk print return order"""
        return f"""
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Laporan Pengembalian - {return_id}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f5f5; }}
        .container {{ max-width: 800px; margin: 20px auto; background: white; padding: 40px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}

        .header {{ text-align: center; margin-bottom: 30px; border-bottom: 2px solid #e0e0e0; padding-bottom: 20px; }}
        .header h1 {{ font-size: 28px; color: #333; margin-bottom: 5px; }}
        .header p {{ color: #999; font-size: 12px; }}

        .content {{ margin: 30px 0; }}
        .section {{ margin-bottom: 25px; }}
        .section-title {{ color: #2563eb; font-weight: bold; font-size: 13px; margin-bottom: 12px; text-transform: uppercase; }}

        .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
        .info-box {{ }}
        .info-label {{ color: #999; font-size: 11px; font-weight: 500; margin-bottom: 3px; }}
        .info-value {{ color: #333; font-size: 13px; font-weight: 600; }}

        table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        th {{ background: #f9f9f9; border: 1px solid #e0e0e0; padding: 10px; text-align: left; font-size: 12px; font-weight: 600; color: #666; }}
        td {{ border: 1px solid #e0e0e0; padding: 10px; font-size: 12px; color: #333; }}

        .total-section {{ background: #f9f9f9; padding: 15px; border: 1px solid #e0e0e0; text-align: right; }}
        .total-row {{ display: flex; justify-content: flex-end; margin-bottom: 8px; }}
        .total-label {{ color: #666; margin-right: 20px; font-size: 12px; }}
        .total-value {{ color: #333; font-weight: 600; font-size: 12px; min-width: 80px; text-align: right; }}

        .footer {{ margin-top: 40px; border-top: 2px solid #e0e0e0; padding-top: 20px; text-align: center; }}
        .footer-line {{ display: flex; justify-content: space-between; font-size: 11px; color: #999; margin-bottom: 3px; }}

        .notes {{ background: #fffbf0; border-left: 3px solid #fb923c; padding: 12px; margin-top: 15px; }}
        .notes-label {{ font-weight: 600; color: #333; font-size: 12px; margin-bottom: 5px; }}
        .notes-value {{ color: #666; font-size: 12px; white-space: pre-wrap; }}

        @media print {{
            body {{ background: white; }}
            .container {{ box-shadow: none; margin: 0; padding: 20px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header dengan ID Pengembalian -->
        <div class="header">
            <h1>🎈 No. Pengembalian #{return_id}</h1>
            <p>{creator_name}</p>
        </div>

        <!-- Tanggal & No. Pesanan -->
        <div class="content">
            <div style="text-align: right; margin-bottom: 30px; font-size: 12px; color: #666;">
                <strong>Tanggal Pengembalian :</strong> {return_info.get('tanggal', '-')}
            </div>

            <!-- Pelanggan -->
            <div class="section">
                <div class="section-title">Pelanggan</div>
                <div class="info-grid">
                    <div class="info-box">
                        <div class="info-label">Nama</div>
                        <div class="info-value">{order.nama or '-'}</div>
                    </div>
                    <div class="info-box">
                        <div class="info-label">No. WA</div>
                        <div class="info-value">{order.nomor_wa or 'N/A'}</div>
                    </div>
                </div>
                <div class="info-box">
                    <div class="info-label">No. Pesanan</div>
                    <div class="info-value" style="font-family: monospace;">#{order.id}</div>
                </div>
            </div>

            <!-- Tabel Produk -->
            <div class="section">
                <div class="section-title">Deskripsi</div>
                <table>
                    <thead>
                        <tr>
                            <th>Deskripsi</th>
                            <th style="width: 80px;">Qty</th>
                            <th style="width: 100px;">Harga</th>
                            <th style="width: 100px;">Total Harga</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr style="background: #fafafa;">
                            <td colspan="4" style="text-align: center; padding: 20px; color: #999; font-style: italic;">Total Pengembalian</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Total Pengembalian -->
            <div class="total-section">
                <div class="total-row">
                    <span class="total-label">Subtotal</span>
                    <span class="total-value">IDR 0</span>
                </div>
                <div class="total-row">
                    <span class="total-label">Tambahan</span>
                    <span class="total-value">IDR 0</span>
                </div>
                <div class="total-row" style="font-size: 13px; font-weight: bold; color: #333; margin-top: 10px; border-top: 1px solid #e0e0e0; padding-top: 10px;">
                    <span class="total-label">Total</span>
                    <span class="total-value">IDR 0</span>
                </div>
            </div>

            <!-- Catatan -->
            {f'<div class="notes"><div class="notes-label">Catatan :</div><div class="notes-value">{return_info.get("catatan", "-")}</div></div>' if return_info.get('catatan') else ''}
        </div>

        <!-- Footer -->
        <div class="footer">
            <div class="footer-line">
                <span><strong>Diketahui oleh:</strong> {creator_name}</span>
                <span><strong>Tanggal Cetak:</strong> {datetime.datetime.now().strftime('%d-%b-%Y')}</span>
            </div>
        </div>
    </div>

    <script>
        // Auto-print saat halaman selesai load
        // window.print();
    </script>
</body>
</html>
"""

    @action(detail=True, methods=['post'], url_path='bayar')
    def bayar(self, request, pk=None):
        order = self.get_object()
        jumlah_bayar = request.data.get('jumlah_bayar')
        metode = request.data.get('metode_pembayaran', 'tunai')

        if jumlah_bayar is None:
            return Response({'error': 'jumlah_bayar wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            jumlah_bayar = int(jumlah_bayar)
        except (ValueError, TypeError):
            return Response({'error': 'jumlah_bayar harus berupa angka bulat.'}, status=status.HTTP_400_BAD_REQUEST)

        order.dp_dibayar += jumlah_bayar
        order.metode_pembayaran = metode
        order._current_user = request.user
        order.save()

        # Buku Besar Otomatis untuk Pelunasan / Cicilan
        record_payment_to_general_ledger(
            order=order,
            jumlah_bayar=jumlah_bayar,
            metode=metode,
            is_dp=False
        )

        # Update statistik Contact
        try:
            contact = Contact.objects.get(nomor_wa=order.nomor_wa)
            my_orders = Order.objects.filter(nomor_wa=contact.nomor_wa).prefetch_related('items')
            contact.total_spent = sum(
                item.harga_jual
                for o in my_orders
                for item in o.items.all()
            )
            contact.save()
        except Contact.DoesNotExist:
            pass

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='import-status-csv', parser_classes=[MultiPartParser])
    def import_status_csv(self, request):
        """
        POST /api/orders/import-status-csv/
        Impor status pesanan massal dari CSV (max. 500 baris) dengan validasi.
        Legenda: P = Tunda, A = Dikonfirmasi, S = Dikirim, T = Terkirim, Z = Selesai, X = Batal
        """
        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': 'File CSV tidak ditemukan.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = upload.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV dengan encoding UTF-8.'}, status=status.HTTP_400_BAD_REQUEST)

        rows = list(csv.DictReader(io.StringIO(decoded)))
        MAX_IMPORT_ROWS = 500
        if len(rows) > MAX_IMPORT_ROWS:
            return Response(
                {'error': f'Maksimal {MAX_IMPORT_ROWS} baris per import (file ini berisi {len(rows)} baris).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not rows:
            return Response({'error': 'File CSV kosong atau tidak memiliki baris data.'}, status=status.HTTP_400_BAD_REQUEST)

        # Mencocokkan nama header secara fleksibel (case-insensitive)
        first_row = rows[0]
        order_id_key = None
        status_key = None
        shipping_date_key = None

        for k in first_row.keys():
            k_clean = k.strip().lower()
            if k_clean in ['no. pesanan', 'no pesanan', 'order id', 'order_id', 'no', 'order_no']:
                order_id_key = k
            elif k_clean in ['status', 'status_code', 'status code', 'update_status', 'update status']:
                status_key = k
            elif k_clean in ['tanggal kirim', 'tanggal_kirim', 'shipping date', 'shipping_date']:
                shipping_date_key = k

        if not order_id_key:
            return Response(
                {'error': 'Header CSV harus memuat kolom nomor pesanan ("order_no" atau "No. Pesanan").'},
                status=status.HTTP_400_BAD_REQUEST
            )

        status_map = {
            'P': 'review',   # Tunda -> Menunggu Review
            'A': 'desain',   # Dikonfirmasi -> Proses Desain
            'S': 'proses',   # Dikirim -> Proses Produksi
            'T': 'ready',    # Terkirim -> Siap Diambil
            'Z': 'selesai',  # Selesai -> Selesai
            'X': 'batal'     # Batal -> Dibatalkan
        }

        row_errors = []
        orders_to_update = []
        seen_order_ids = set()

        for idx, row in enumerate(rows, start=2):  # Baris 1 adalah header
            order_id = (row.get(order_id_key) or '').strip()
            status_code = (row.get(status_key) or '').strip().upper() if status_key else ''
            shipping_date_str = (row.get(shipping_date_key) or '').strip() if shipping_date_key else ''

            if not order_id:
                # Lewati jika baris benar-benar kosong
                if not status_code and not shipping_date_str:
                    continue
                row_errors.append({
                    'row': idx,
                    'order_id': '-',
                    'tanggal_kirim': shipping_date_str,
                    'message': 'No. Pesanan (order_no) wajib diisi.'
                })
                continue

            if order_id in seen_order_ids:
                row_errors.append({
                    'row': idx,
                    'order_id': order_id,
                    'tanggal_kirim': shipping_date_str,
                    'message': f'No. Pesanan "{order_id}" duplikat dalam berkas CSV.'
                })
                continue
            seen_order_ids.add(order_id)

            # Jika status dan tanggal kirim dua-duanya kosong untuk order_id ini, lewati saja
            if not status_code and not shipping_date_str:
                continue

            new_status = None
            if status_code:
                if status_code not in status_map:
                    row_errors.append({
                        'row': idx,
                        'order_id': order_id,
                        'tanggal_kirim': shipping_date_str,
                        'message': f'Kode status "{status_code}" tidak valid. Gunakan P, A, S, T, Z, atau X.'
                    })
                    continue
                new_status = status_map[status_code]

            try:
                order = Order.objects.get(id=order_id)
            except Order.DoesNotExist:
                row_errors.append({
                    'row': idx,
                    'order_id': order_id,
                    'tanggal_kirim': shipping_date_str,
                    'message': f'Pesanan dengan ID "{order_id}" tidak ditemukan.'
                })
                continue

            # Validasi format tanggal kirim jika ada
            parsed_date = None
            if shipping_date_str:
                success = False
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d', '%m/%d/%Y', '%m-%d-%Y'):
                    try:
                        parsed_date = datetime.datetime.strptime(shipping_date_str, fmt).date()
                        success = True
                        break
                    except ValueError:
                        continue
                if not success:
                    row_errors.append({
                        'row': idx,
                        'order_id': order_id,
                        'tanggal_kirim': shipping_date_str,
                        'message': f'Format Tanggal Kirim "{shipping_date_str}" tidak valid. Gunakan YYYY-MM-DD, DD/MM/YYYY, atau MM/DD/YYYY.'
                    })
                    continue

            orders_to_update.append((order, new_status, parsed_date, shipping_date_str))

        if row_errors:
            return Response({'errors': row_errors}, status=status.HTTP_400_BAD_REQUEST)

        # Jika hanya dry-run/validasi saja, return OK tanpa menyimpan
        if request.query_params.get('dry_run') == 'true':
            return Response({
                'success': True,
                'message': f'Semua {len(orders_to_update)} baris valid.',
                'valid_count': len(orders_to_update)
            }, status=status.HTTP_200_OK)

        # Proses pembaruan dalam satu transaksi database atomic
        updated_count = 0
        with transaction.atomic():
            for order, new_status, parsed_date, orig_date_str in orders_to_update:
                changed = False
                ket_parts = []

                if new_status and order.status_global != new_status:
                    order.status_global = new_status
                    changed = True
                    ket_parts.append(f"Status diperbarui menjadi '{new_status}'")

                if orig_date_str:
                    ket_parts.append(f"Tanggal Kirim: {orig_date_str}")
                    changed = True

                if changed:
                    order._current_user = request.user
                    order.save()

                    ket = ", ".join(ket_parts) + " via impor CSV."
                    OrderActivityLog.objects.create(
                        order=order,
                        user=request.user,
                        tindakan="UPDATE_STATUS",
                        keterangan=ket
                    )
                    updated_count += 1

        return Response({
            'success': True,
            'message': f'Berhasil memperbarui status {updated_count} pesanan.',
            'updated_count': updated_count
        }, status=status.HTTP_200_OK)



class AssignOrderView(APIView):
    """POST /api/orders/{order_id}/assign/ — publish/assign SPK ke semua item dalam order.

    Kasir ikut diizinkan karena menerbitkan SPK adalah bagian dari alur Buat
    Order di terminal kasir, tetapi hanya ke antrean divisi. Penunjukan staff
    tertentu ditolak di spk.resolve_staff().
    """
    permission_classes = [IsOwnerManagerAdminOrKasir]

    def post(self, request, order_id):
        staff_id = request.data.get('staff_id')  # ditolak bila pemohon kasir
        tahap_id = request.data.get('tahap_id', None)
        divisi_id = request.data.get('divisi_id', None)
        status_global = request.data.get('status_global', None)
        try:
            biaya_desain = int(request.data.get('biaya_desain', 0) or 0)
        except (ValueError, TypeError):
            biaya_desain = 0
        try:
            insentif = int(request.data.get('insentif', 0) or 0)
        except (ValueError, TypeError):
            insentif = 0

        order_item_id = request.data.get('order_item_id', None)

        # Validasi order ada
        try:
            order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'Order tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        # Resolusi staff/tahap dipusatkan di api/spk.py — dipakai bersama
        # dengan penerbitan SPK dari terminal POS agar aturannya tidak bercabang.
        try:
            staff = spk.resolve_staff(staff_id, pemohon=request.user)
            tahap = spk.resolve_tahap(tahap_id=tahap_id, divisi_id=divisi_id, staff=staff)
        except spk.SpkError as exc:
            return Response({'error': exc.pesan}, status=exc.status_code)

        # Buat atau update JobBoard untuk setiap OrderItem
        items = order.items.all()
        if order_item_id:
            items = items.filter(pk=order_item_id)

        if not items.exists():
            return Response({'error': 'Order ini belum memiliki item produk atau item tidak cocok.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            created_jobs = spk.terbitkan(
                items, field='order_item', tahap=tahap, staff=staff,
                biaya_desain=biaya_desain, insentif=insentif,
            )
        except spk.SpkError as exc:
            return Response({'error': exc.pesan}, status=exc.status_code)

        target_name = spk.nama_target(staff, tahap)
        tahap_desc = tahap.nama if tahap else "Tahap Awal"
        action_desc = "TUGASKAN_STAFF" if staff else "TERBITKAN_SPK"
        for item in items:
            OrderActivityLog.objects.create(
                order=order,
                user=request.user,
                tindakan=action_desc,
                keterangan=f"Menerbitkan SPK item '{item.jenis_produk}' ke {target_name} untuk tahap '{tahap_desc}'"
            )

        # Update status order
        if status_global:
            order.status_global = status_global
        else:
            # Fallback cerdas berdasarkan divisi staff/tahap
            is_desain = False
            if tahap and tahap.divisi and tahap.divisi.nama.lower() == 'desain':
                is_desain = True
            elif staff and staff.divisi and staff.divisi.nama.lower() == 'desain':
                is_desain = True
                
            if is_desain:
                order.status_global = 'desain'
            else:
                order.status_global = 'proses'
        
        order._current_user = request.user
        order.save()

        return Response({
            'message': f'Order {order_id} berhasil di-publish/assign ke {target_name}.',
            'jobs': created_jobs,
        }, status=status.HTTP_200_OK)


class OrderItemViewSet(viewsets.ModelViewSet):
    queryset = OrderItem.objects.select_related(
        'order'
    ).order_by('-id')
    serializer_class = OrderItemSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(_current_user=self.request.user)

    def perform_update(self, serializer):
        serializer.instance._current_user = self.request.user
        serializer.save()

    def perform_destroy(self, instance):
        instance._current_user = self.request.user
        instance.delete()


class ForwardJobView(APIView):
    """
    POST /api/jobs/{job_id}/forward/
    Body:
      aksi        : 'forward' | 'selesai'
      tahap_id    : (wajib jika aksi='forward') ID TahapProses tujuan
      pic_staff_id: (opsional) ID staff untuk tahap baru
    """
    permission_classes = [IsAuthenticated, IsClockedIn]

    def post(self, request, job_id):
        # Ambil job
        try:
            job = JobBoard.objects.get(pk=job_id)
        except JobBoard.DoesNotExist:
            return Response({'error': 'Job tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        # Staff hanya bisa forward job miliknya
        if request.user.role == 'staff' and job.pic_staff != request.user:
            return Response({'error': 'Anda tidak memiliki akses ke job ini.'}, status=status.HTTP_403_FORBIDDEN)

        aksi         = request.data.get('aksi')          # 'forward' atau 'selesai'
        tahap_id     = request.data.get('tahap_id')
        pic_staff_id = request.data.get('pic_staff_id')

        # Guard: jangan proses job yang sudah selesai/gagal (mencegah duplikasi)
        if job.status_pekerjaan in ('selesai', 'gagal'):
            return Response(
                {'error': f'Job sudah berstatus "{job.status_pekerjaan}", tidak bisa diforward ulang.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validasi tahap_id dan ambil tahap_baru SEBELUM transaction.atomic()
        tahap_baru = None
        if aksi == 'forward':
            if not tahap_id:
                return Response(
                    {'error': 'tahap_id wajib diisi jika aksi=forward.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Ambil tahap tujuan
            try:
                tahap_baru = TahapProses.objects.get(pk=tahap_id)
            except TahapProses.DoesNotExist:
                return Response({'error': 'Tahap tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            # Tandai job saat ini sebagai SELESAI
            job.status_pekerjaan = 'selesai'
            job.waktu_selesai    = timezone.now()
            job.otp_code         = ''
            job.otp_requested    = False
            job.otp_sent         = False
            job.save()

            # Potong bahan otomatis ke inventori
            deduct_job_materials_if_needed(job, request.user)

            if aksi == 'forward':
                # Siapkan catatan dari divisi sebelumnya
                catatan_sebelumnya = job.catatan_staff if isinstance(job.catatan_staff, list) else []
                if catatan_sebelumnya or job.gdrive_output_link:
                    separator = {
                        "keterangan": f"--- Dari Divisi: {job.tahap.nama if job.tahap else 'Sebelumnya'} ---",
                        "qty": "-",
                        "satuan": "-",
                        "catatan": f"Oleh: {job.pic_staff.username if job.pic_staff else 'Staff'}",
                        "gdrive_link": job.gdrive_output_link or ""  # ← link file dari divisi sebelumnya
                    }
                    catatan_sebelumnya = catatan_sebelumnya + [separator]

                # Cek apakah sudah ada job untuk tahap ini di order item yang sama
                existing = JobBoard.objects.filter(
                    order_item=job.order_item, tahap=tahap_baru
                ).first()

                if existing:
                    # Reset job yang sudah ada ke antrean
                    existing.status_pekerjaan = 'antrean'
                    existing.waktu_mulai      = None
                    existing.waktu_selesai    = None
                    
                    # Gabung catatan lama dengan catatan dari divisi sebelumnya
                    existing_cat = existing.catatan_staff if isinstance(existing.catatan_staff, list) else []
                    existing.catatan_staff = existing_cat + catatan_sebelumnya
                    
                    if pic_staff_id:
                        try:
                            existing.pic_staff = CustomUser.objects.get(pk=pic_staff_id, role='staff')
                        except CustomUser.DoesNotExist:
                            pass
                    existing.save()
                    new_job_id = existing.id
                else:
                    # Buat job baru
                    new_job = JobBoard(
                        order_item      = job.order_item,
                        tahap           = tahap_baru,
                        status_pekerjaan = 'antrean',
                        catatan_staff   = catatan_sebelumnya
                    )
                    if pic_staff_id:
                        try:
                            new_job.pic_staff = CustomUser.objects.get(pk=pic_staff_id, role='staff')
                        except CustomUser.DoesNotExist:
                            pass
                    new_job.save()
                    new_job_id = new_job.id

                return Response({
                    'message': f'Job diteruskan ke tahap "{tahap_baru.nama}" (Divisi: {tahap_baru.divisi.nama}).',
                    'new_job_id': new_job_id,
                }, status=status.HTTP_201_CREATED)

            elif aksi == 'selesai':
                # Cek apakah seluruh job dari semua item dalam pesanan ini sudah selesai
                order = job.order_item.order
                active_jobs_exist = JobBoard.objects.filter(
                    order_item__order=order,
                    status_pekerjaan__in=['antrean', 'dikerjakan', 'kendala']
                ).exists()

                if not active_jobs_exist:
                    order.status_global = 'ready'
                    order.save()

                return Response(
                    {'message': 'Job ditandai selesai. Tidak ada tahap lanjutan.' + (' Order secara global telah siap diambil (READY).' if not active_jobs_exist else '')},
                    status=status.HTTP_200_OK
                )

            return Response({'error': 'Aksi tidak valid. Gunakan "forward" atau "selesai".'}, status=status.HTTP_400_BAD_REQUEST)
