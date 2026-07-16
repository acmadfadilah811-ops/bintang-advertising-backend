import uuid
import logging
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Count, Sum

from ..models import (
    Order, OrderItem, JobBoard, CustomUser, Contact, OrderActivityLog, TahapProses
)
from ..serializers import (
    OrderSerializer, OrderItemSerializer, JobBoardSerializer
)
from ..permissions import IsOwnerOrManager, IsClockedIn
from hr.models import Akun, TransaksiBukuBesar
from users.models import SecurityAuditLog

# Imported from legacy for now; will be moved to jobs.py later
from api.views.legacy import deduct_job_materials_if_needed

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
            'items__jobs__pic_staff__divisi'
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
            
        if user.role in ['owner', 'manager', 'admin']:
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
                block_pay=instance.dp_dibayar,  # Wait! Is it jumlah_bayar? Yes, the parameter is jumlah_bayar
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


class AssignOrderView(APIView):
    """POST /api/orders/{order_id}/assign/ — Manager/Admin publish/assign staff ke semua item dalam order"""
    permission_classes = [IsOwnerOrManager]

    def post(self, request, order_id):
        staff_id = request.data.get('staff_id')
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

        staff = None
        tahap = None

        if staff_id:
            # Validasi staff ada
            try:
                staff = CustomUser.objects.get(pk=staff_id, role='staff')
            except CustomUser.DoesNotExist:
                return Response({'error': 'Staff tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)

        # Ambil tahap jika ada
        if tahap_id:
            try:
                tahap = TahapProses.objects.get(pk=tahap_id)
            except Exception as e:
                logger.warning(f"Gagal mengambil TahapProses dengan id {tahap_id}: {e}")

        # JIKA tahap masih None tapi ada divisi_id, ambil tahap pertama di divisi tersebut
        if not tahap and divisi_id:
            try:
                tahap = TahapProses.objects.filter(divisi_id=divisi_id).order_by('urutan').first()
            except Exception as e:
                logger.warning(f"Gagal mengambil TahapProses untuk divisi_id {divisi_id}: {e}")

        # JIKA tahap masih None tapi staff diisi, ambil tahap pertama divisi staff sebagai fallback
        if not tahap and staff and staff.divisi:
            try:
                tahap = TahapProses.objects.filter(divisi=staff.divisi).order_by('urutan').first()
            except Exception as e:
                logger.warning(f"Gagal mengambil TahapProses fallback untuk staff {staff.id}: {e}")

        # Guard: setidaknya harus ada tahap/divisi jika tidak ada staff
        if not tahap and not staff:
            return Response({'error': 'staff_id atau divisi/tahap wajib diisi untuk penerbitan SPK.'}, status=status.HTTP_400_BAD_REQUEST)

        # Buat atau update JobBoard untuk setiap OrderItem
        items = order.items.all()
        if order_item_id:
            items = items.filter(pk=order_item_id)
            
        if not items.exists():
            return Response({'error': 'Order ini belum memiliki item produk atau item tidak cocok.'}, status=status.HTTP_400_BAD_REQUEST)

        # Buat atau update JobBoard — lookup by (order_item, tahap) bukan hanya order_item
        created_jobs = []
        target_name = staff.username if staff else (tahap.divisi.nama if tahap and tahap.divisi else 'Divisi')
        tahap_desc = tahap.nama if tahap else "Tahap Awal"
        
        for item in items:
            job, created = JobBoard.objects.update_or_create(
                order_item=item,
                tahap=tahap,          # <-- tambahkan tahap ke lookup key
                defaults={
                    'pic_staff': staff,
                    'status_pekerjaan': 'antrean',
                    'biaya_desain': biaya_desain,
                    'insentif': insentif,
                    'waktu_mulai': None,
                    'waktu_selesai': None,
                }
            )
            created_jobs.append({'job_id': job.id, 'item': item.jenis_produk, 'created': created})
            
            # Log penugasan/penerbitan SPK
            action_desc = "TUGASKAN_STAFF" if staff else "TERBITKAN_SPK"
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
