from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard, InventoryItem, ProductPrice, SystemConfig, FAQ, OrderActivityLog

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    fieldsets = UserAdmin.fieldsets + (
        ('Informasi Tambahan Bintang Adv', {'fields': ('role', 'divisi', 'no_hp', 'kota', 'negara', 'alamat', 'bio')}),
    )
    list_display = ['username', 'role', 'divisi', 'is_staff']
    list_filter = ['role', 'divisi']

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1

class OrderActivityLogInline(admin.TabularInline):
    model = OrderActivityLog
    readonly_fields = ['user', 'tindakan', 'keterangan', 'waktu']
    extra = 0
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False

class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'nama', 'nomor_wa', 'status_global', 'waktu']
    list_filter = ['status_global']
    search_fields = ['id', 'nama', 'nomor_wa']
    inlines = [OrderItemInline, OrderActivityLogInline]

class JobBoardAdmin(admin.ModelAdmin):
    list_display = ['get_sumber', 'get_produk', 'tahap', 'pic_staff', 'status_pekerjaan', 'insentif']
    list_filter = ['status_pekerjaan', 'tahap__divisi', 'tahap']
    # SPK bisa berasal dari order maupun nota POS, jadi keduanya bisa dicari.
    search_fields = ['order_item__order__id', 'pos_sale_item__sale__nomor', 'pic_staff__username']

    def get_sumber(self, obj):
        return obj.nomor_sumber
    get_sumber.short_description = 'Order / Nota'

    def get_produk(self, obj):
        return obj.nama_produk
    get_produk.short_description = 'Jenis Produk'

class OrderActivityLogAdmin(admin.ModelAdmin):
    list_display = ['order', 'user', 'tindakan', 'keterangan', 'waktu']
    list_filter = ['tindakan', 'waktu']
    search_fields = ['order__id', 'user__username', 'keterangan']
    readonly_fields = ['order', 'user', 'tindakan', 'keterangan', 'waktu']

    def has_add_permission(self, request):
        return False

admin.site.register(CustomUser, CustomUserAdmin)
admin.site.register(Divisi)
admin.site.register(TahapProses)
admin.site.register(Contact)
admin.site.register(Order, OrderAdmin)
admin.site.register(JobBoard, JobBoardAdmin)
admin.site.register(InventoryItem)
admin.site.register(ProductPrice)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'value')
    search_fields = ('key', 'value')
    ordering = ('key',)

admin.site.register(SystemConfig, SystemConfigAdmin)
admin.site.register(FAQ)
admin.site.register(OrderActivityLog, OrderActivityLogAdmin)