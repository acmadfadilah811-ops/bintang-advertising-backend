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


def cleanup_old_activity_logs(days=90):
    """
    Hapus SecurityAuditLog yang lebih tua dari N hari (default 90 hari).
    Mencegah tabel audit log tumbuh tidak terbatas.
    """
    from users.models import SecurityAuditLog

    cutoff = timezone.now() - timedelta(days=days)
    deleted_count, _ = SecurityAuditLog.objects.filter(
        timestamp__lt=cutoff
    ).delete()

    logger.info(f"[cleanup] Menghapus {deleted_count} audit log lama (>{ days} hari).")
    return deleted_count


def cleanup_old_order_activity_logs(days=180):
    """
    Hapus OrderActivityLog yang lebih tua dari N hari (default 180 hari).
    """
    from .models import OrderActivityLog

    cutoff = timezone.now() - timedelta(days=days)
    deleted_count, _ = OrderActivityLog.objects.filter(
        waktu__lt=cutoff
    ).delete()

    logger.info(f"[cleanup] Menghapus {deleted_count} order activity log lama (>{days} hari).")
    return deleted_count
