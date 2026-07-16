import os
import uuid
import logging
from django.conf import settings
from django.db import connection
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle

from ..models import Order, OrderItem, OrderActivityLog

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """GET /api/health/ — Cek status semua komponen sistem."""
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def get(self, request):
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


class ClientLogView(APIView):
    """
    POST /api/log-client-error/
    Logs frontend/client-side uncaught crashes to backend log files.
    """
    permission_classes = []  # Allow anonymous logging if crash occurs before login config is fully resolved
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
