"""
api/tasks.py — Background maintenance tasks.

Jalankan manual (management command) atau jadwalkan via cron/celery-beat:
  # Cron (setiap malam jam 02:00):
  0 2 * * * /path/to/.venv/bin/python /path/to/manage.py cleanup_expired_tokens

Atau via Django management command:
  python manage.py cleanup_expired_tokens
"""
import logging
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


def cleanup_expired_session_tokens():
    """
    Nonaktifkan semua token JWT/Session yang sudah melewati expires_at.
    Mencegah tabel SessionToken membengkak dengan data expired.
    """
    from users.models import SessionToken

    cutoff = timezone.now()
    expired_count = SessionToken.objects.filter(
        expires_at__lt=cutoff,
        is_active=True
    ).update(is_active=False, revoked_at=cutoff)

    logger.info(f"[cleanup] Menonaktifkan {expired_count} session token expired.")
    return expired_count


def cleanup_old_activity_logs(days=90, batch_size=1000):
    """
    Hapus SecurityAuditLog yang lebih tua dari N hari (default 90 hari).
    Mencegah tabel audit log tumbuh tidak terbatas.
    Menggunakan batching untuk menghindari database table lock.
    """
    from users.models import SecurityAuditLog

    cutoff = timezone.now() - timedelta(days=days)
    total_deleted = 0
    while True:
        ids = list(SecurityAuditLog.objects.filter(
            timestamp__lt=cutoff
        ).values_list('id', flat=True)[:batch_size])
        if not ids:
            break
        deleted_count, _ = SecurityAuditLog.objects.filter(id__in=ids).delete()
        total_deleted += deleted_count

    logger.info(f"[cleanup] Menghapus {total_deleted} audit log lama (>{days} hari) dalam batch.")
    return total_deleted


def cleanup_old_order_activity_logs(days=180, batch_size=1000):
    """
    Hapus OrderActivityLog yang lebih tua dari N hari (default 180 hari).
    Menggunakan batching untuk menghindari database table lock.
    """
    from .models import OrderActivityLog

    cutoff = timezone.now() - timedelta(days=days)
    total_deleted = 0
    while True:
        ids = list(OrderActivityLog.objects.filter(
            waktu__lt=cutoff
        ).values_list('id', flat=True)[:batch_size])
        if not ids:
            break
        deleted_count, _ = OrderActivityLog.objects.filter(id__in=ids).delete()
        total_deleted += deleted_count

    logger.info(f"[cleanup] Menghapus {total_deleted} order activity log lama (>{days} hari) dalam batch.")
    return total_deleted
