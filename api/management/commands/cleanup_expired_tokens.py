"""
Management command: cleanup_expired_tokens
Jalankan manual: python manage.py cleanup_expired_tokens
Jadwalkan via cron: 0 2 * * * /path/venv/bin/python /path/manage.py cleanup_expired_tokens
"""
from django.core.management.base import BaseCommand
from api.tasks import (
    cleanup_expired_session_tokens,
    cleanup_old_activity_logs,
    cleanup_old_order_activity_logs,
)


class Command(BaseCommand):
    help = 'Cleanup expired session tokens dan log lama untuk menjaga performa database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--audit-days', type=int, default=90,
            help='Hapus SecurityAuditLog lebih tua dari N hari (default: 90)'
        )
        parser.add_argument(
            '--activity-days', type=int, default=180,
            help='Hapus OrderActivityLog lebih tua dari N hari (default: 180)'
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Tampilkan preview tanpa benar-benar menghapus'
        )

    def handle(self, *args, **options):
        audit_days = options['audit_days']
        activity_days = options['activity_days']
        dry_run = options['dry_run']

        if dry_run:
            from django.utils import timezone
            from datetime import timedelta
            from users.models import SessionToken, SecurityAuditLog
            from api.models import OrderActivityLog

            token_count = SessionToken.objects.filter(
                expires_at__lt=timezone.now(), is_active=True
            ).count()
            audit_count = SecurityAuditLog.objects.filter(
                timestamp__lt=timezone.now() - timedelta(days=audit_days)
            ).count()
            activity_count = OrderActivityLog.objects.filter(
                waktu__lt=timezone.now() - timedelta(days=activity_days)
            ).count()

            self.stdout.write(self.style.WARNING('--- DRY RUN (tidak ada yang dihapus) ---'))
            self.stdout.write(f'  Session token expired yang akan dinonaktifkan: {token_count}')
            self.stdout.write(f'  Audit log >{audit_days} hari yang akan dihapus: {audit_count}')
            self.stdout.write(f'  Activity log >{activity_days} hari yang akan dihapus: {activity_count}')
            return

        self.stdout.write('Memulai cleanup database...')

        tokens = cleanup_expired_session_tokens()
        self.stdout.write(self.style.SUCCESS(f'  ✅ Session tokens dinonaktifkan: {tokens}'))

        audit = cleanup_old_activity_logs(days=audit_days)
        self.stdout.write(self.style.SUCCESS(f'  ✅ Audit logs dihapus: {audit}'))

        activity = cleanup_old_order_activity_logs(days=activity_days)
        self.stdout.write(self.style.SUCCESS(f'  ✅ Order activity logs dihapus: {activity}'))

        self.stdout.write(self.style.SUCCESS('\nCleanup selesai!'))
