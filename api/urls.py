from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views import DashboardView, CreateUserView, AssignOrderView, ForwardJobView, InventoryRestockView, JobMaterialDeductView, FonnteWebhookView, EvolutionWebhookView, BusinessSettingsView, StaffPerformanceReportView, HealthCheckView, KomplainViewSet
from .export_views import ExportOrdersView, ExportInventoryView, ExportJobsView, ExportContactsView, ExportAbsensiView, ExportStaffPerformanceView

router = DefaultRouter()

router.register(r'divisi', views.DivisiViewSet)
router.register(r'tahap-proses', views.TahapProsesViewSet)
router.register(r'users', views.CustomUserViewSet)
router.register(r'contacts', views.ContactViewSet)
router.register(r'orders',      views.OrderViewSet,    basename='order')
router.register(r'order-items', views.OrderItemViewSet)
router.register(r'jobs',        views.JobBoardViewSet, basename='job')

router.register(r'inventory', views.InventoryItemViewSet, basename='inventory')
router.register(r'product-prices', views.ProductPriceViewSet)
router.register(r'app-config', views.SystemConfigViewSet)
router.register(r'faq', views.FAQViewSet)
router.register(r'komplain', views.KomplainViewSet, basename='komplain')
router.register(r'customer-activities', views.CustomerActivityViewSet, basename='customer-activity')
router.register(r'bom', views.BillOfMaterialsViewSet, basename='bom')
router.register(r'bom-items', views.BoMItemViewSet, basename='bom-item')


urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('auth/create-user/', CreateUserView.as_view(), name='create_user'),
    path('orders/<str:order_id>/assign/', AssignOrderView.as_view(), name='assign_order'),
    path('jobs/<int:job_id>/forward/', ForwardJobView.as_view(), name='forward_job'),
    path('jobs/<int:job_id>/use-materials/', JobMaterialDeductView.as_view(), name='job-use-materials'),
    
    # Export Endpoints
    path('export/orders/', ExportOrdersView.as_view(), name='export-orders'),
    path('export/inventory/', ExportInventoryView.as_view(), name='export-inventory'),
    path('export/jobs/', ExportJobsView.as_view(), name='export-jobs'),
    path('export/contacts/', ExportContactsView.as_view(), name='export-contacts'),
    path('export/absensi/', ExportAbsensiView.as_view(), name='export-absensi'),
    path('export/staff-performance/', ExportStaffPerformanceView.as_view(), name='export-staff-performance'),

    # Reports Endpoints
    path('reports/staff-performance/', StaffPerformanceReportView.as_view(), name='staff-performance-report'),

    # Explicit restock URL — standalone APIView, tidak pakai @action ViewSet
    path('inventory/<str:pk>/restock/', InventoryRestockView.as_view(), name='inventory-restock'),
    
    # Webhook Bot WA (Fonnte)
    path('webhook/fonnte/', FonnteWebhookView.as_view(), name='webhook-fonnte'),

    # Webhook Bot WA (Evolution API)
    path('webhook/evolution/', EvolutionWebhookView.as_view(), name='webhook-evolution'),

    # Business Settings (mirip OrgSettings di Django CRM)
    path('business-settings/', BusinessSettingsView.as_view(), name='business-settings'),
    
    # Health check
    path('health/', HealthCheckView.as_view(), name='health-check'),
]