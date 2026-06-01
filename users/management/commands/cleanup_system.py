from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from users.models import SessionToken, SecurityAuditLog
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

class Command(BaseCommand):
    help = 'Membersihkan data usang (Session Token kadaluarsa, Audit Log >90 hari, dan Blacklisted JWT)'

    def handle(self, *args, **options):
        # 1. Session Token
        count_sessions, _ = SessionToken.cleanup_expired()
        self.stdout.write(self.style.SUCCESS(f'Berhasil menghapus {count_sessions} Session Token kadaluarsa.'))

        # 2. Audit Log > 90 hari
        cut_off = timezone.now() - timedelta(days=90)
        count_logs, _ = SecurityAuditLog.objects.filter(waktu__lt=cut_off).delete()
        self.stdout.write(self.style.SUCCESS(f'Berhasil menghapus {count_logs} Security Audit Log yang lebih tua dari 90 hari.'))

        # 3. Simple JWT Outstanding Token kadaluarsa
        count_jwt, _ = OutstandingToken.objects.filter(expires_at__lt=timezone.now()).delete()
        self.stdout.write(self.style.SUCCESS(f'Berhasil menghapus {count_jwt} JWT Outstanding/Blacklisted Token kadaluarsa.'))
