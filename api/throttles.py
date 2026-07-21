from rest_framework.throttling import AnonRateThrottle, UserRateThrottle, SimpleRateThrottle

class ExportRateThrottle(UserRateThrottle):
    scope = 'export'

class ReportRateThrottle(UserRateThrottle):
    scope = 'report'

class PasskeyRateThrottle(UserRateThrottle):
    scope = 'passkey'

class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'

class PasswordResetRequestThrottle(AnonRateThrottle):
    scope = 'password_reset_request'

class PasswordResetVerifyThrottle(SimpleRateThrottle):
    scope = 'password_reset_verify'
    def get_cache_key(self, request, view):
        username = str(request.data.get('username', '')).strip().lower()
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': f'{username}:{ident}'}
