from django.contrib import admin

from .models import Profile, SecurityAuditLog, SessionToken


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "is_organization_admin", "has_production_access", "has_finance_access", "is_online_display", "last_seen"]
    list_filter = ["is_organization_admin", "has_finance_access"]
    search_fields = ["user__username", "user__email"]
    readonly_fields = ["last_seen"]

    def role(self, obj):
        return obj.user.role
    role.short_description = "Role"

    def is_online_display(self, obj):
        return "🟢 Online" if obj.is_online else "⚫ Offline"
    is_online_display.short_description = "Status"


@admin.register(SessionToken)
class SessionTokenAdmin(admin.ModelAdmin):
    list_display = ["user", "device_name", "ip_address", "is_active", "last_used_at", "expires_at"]
    list_filter = ["is_active"]
    search_fields = ["user__username", "ip_address", "device_name"]
    readonly_fields = ["token_jti", "created_at", "last_used_at"]
    actions = ["revoke_selected"]

    @admin.action(description="Cabut (revoke) sesi terpilih")
    def revoke_selected(self, request, queryset):
        for session in queryset.filter(is_active=True):
            session.revoke()
        self.message_user(request, f"{queryset.count()} sesi berhasil dicabut.")


@admin.register(SecurityAuditLog)
class SecurityAuditLogAdmin(admin.ModelAdmin):
    list_display = ["waktu", "event", "username_display", "ip_address", "berhasil"]
    list_filter = ["event", "berhasil"]
    search_fields = ["username_input", "ip_address", "user__username"]
    readonly_fields = ["waktu", "user", "username_input", "event", "ip_address", "user_agent", "keterangan", "berhasil"]
    ordering = ["-waktu"]

    def username_display(self, obj):
        return obj.user.username if obj.user else (obj.username_input or "Unknown")
    username_display.short_description = "User"

    def has_add_permission(self, request):
        return False  # Log hanya dibuat oleh sistem
