from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views import DashboardView, CreateUserView, AssignOrderView, ForwardJobView, InventoryRestockView, JobMaterialDeductView, FonnteWebhookView, EvolutionWebhookView, BusinessSettingsView, StaffPerformanceReportView, HealthCheckView, KomplainViewSet, ContactStatsView, ProductionCustomerLiteView
from .export_views import ExportOrdersView, ExportInventoryView, ExportJobsView, ExportContactsView, ExportAbsensiView, ExportStaffPerformanceView, ExportStockMovementView, ExportCustomersView, ExportProductsView, ExportCustomerNotesView, ExportCashTransactionsView, ExportSalesItemsByBrandView, ExportSalesDetailsView
from . import product_views
from . import production_views
from . import marketing_views
from . import customer_views
from . import finance_views
from .report_views import ReportDataView, ReportExportView

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
router.register(r'shift-timing', views.ShiftTimingViewSet, basename='shift-timing')
router.register(r'pos-antrian-device', views.POSAntrianDeviceViewSet, basename='pos-antrian-device')
router.register(r'saldo-kas-harian', views.SaldoKasHarianViewSet, basename='saldo-kas-harian')
router.register(r'ringkasan-shift', views.RingkasanShiftViewSet, basename='ringkasan-shift')
router.register(r'pos-payment-methods', views.POSPaymentMethodViewSet, basename='pos-payment-method')

# Product & Inventory Phase 1
router.register(r'product-categories', product_views.ProductCategoryViewSet, basename='product-category')
router.register(r'brands', product_views.BrandViewSet, basename='brand')
router.register(r'special-types', product_views.SpecialTypeViewSet, basename='special-type')
router.register(r'collections', product_views.CollectionViewSet, basename='collection')
router.register(r'products', product_views.ProductViewSet, basename='product')
router.register(r'product-images', product_views.ProductImageViewSet, basename='product-image')
router.register(r'product-variants', product_views.ProductVariantViewSet, basename='product-variant')
router.register(r'product-packages', product_views.ProductPackageViewSet, basename='product-package')
router.register(r'addons', product_views.AddonViewSet, basename='addon')
router.register(r'specifications', product_views.SpecificationViewSet, basename='specification')
router.register(r'product-spec-values', product_views.ProductSpecValueViewSet, basename='product-spec-value')
router.register(r'product-stock-movements', product_views.ProductStockMovementViewSet, basename='product-stock-movement')
router.register(r'purchases', product_views.PurchaseViewSet, basename='purchase')
router.register(r'cash-transaction-types', finance_views.CashTransactionTypeViewSet, basename='cash-transaction-type')
router.register(r'cash-transactions', finance_views.CashTransactionViewSet, basename='cash-transaction')
router.register(r'stock-in-documents', product_views.StockInDocumentViewSet, basename='stock-in-document')
router.register(r'stock-out-documents', product_views.StockOutDocumentViewSet, basename='stock-out-document')
router.register(r'stock-production-documents', product_views.StockProductionDocumentViewSet, basename='stock-production-document')
router.register(r'production-costs', production_views.ProductionCostViewSet, basename='production-cost')
router.register(r'stock-production-costs', production_views.StockProductionDocumentCostViewSet, basename='stock-production-cost')
router.register(r'stock-opname-documents', product_views.StockOpnameDocumentViewSet, basename='stock-opname-document')

# Marketing (Voucher & Diskon)
router.register(r'sales-discounts', marketing_views.SalesDiscountViewSet, basename='sales-discount')
router.register(r'discount-coupons', marketing_views.DiscountCouponViewSet, basename='discount-coupon')
router.register(r'pos-promotions', marketing_views.POSPromotionViewSet, basename='pos-promotion')

# Pelanggan & Supplier
router.register(r'customer-groups', customer_views.CustomerGroupViewSet, basename='customer-group')
router.register(r'customers', customer_views.CustomerViewSet, basename='customer')
router.register(r'customer-notes', customer_views.CustomerNoteViewSet, basename='customer-note')
router.register(r'customer-note-entries', customer_views.CustomerNoteEntryViewSet, basename='customer-note-entry')
router.register(r'customer-note-documents', customer_views.CustomerNoteDocumentViewSet, basename='customer-note-document')
router.register(r'customer-note-tags', customer_views.CustomerNoteTagViewSet, basename='customer-note-tag')
router.register(r'customer-reviews', customer_views.CustomerReviewViewSet, basename='customer-review')
router.register(r'suppliers', customer_views.SupplierViewSet, basename='supplier')

# POS Cashier Terminal
from . import executive_dashboard_views
from . import pos_views
router.register(r'pos/sales', pos_views.POSSaleViewSet, basename='pos-sale')


urlpatterns = [
    path('contacts/stats/', ContactStatsView.as_view(), name='contact-stats'),
    # BE-24: endpoint sempit papan produksi (nama + no. WA saja, tanpa finansial).
    path('contacts/production-lite/', ProductionCustomerLiteView.as_view(), name='contact-production-lite'),
    path('', include(router.urls)),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    # Dashboard eksekutif (manajemen) — terpisah dari dashboard operasional di atas.
    path('executive-dashboard/', executive_dashboard_views.ExecutiveDashboardView.as_view(), name='executive-dashboard'),
    path('executive-dashboard/export/', executive_dashboard_views.ExecutiveDashboardExportView.as_view(), name='executive-dashboard-export'),
    path('auth/create-user/', CreateUserView.as_view(), name='create_user'),
    path('orders/<str:order_id>/assign/', AssignOrderView.as_view(), name='assign_order'),
    path('jobs/<int:job_id>/forward/', ForwardJobView.as_view(), name='forward_job'),
    path('jobs/<int:job_id>/use-materials/', JobMaterialDeductView.as_view(), name='job-use-materials'),
    
    # Export Endpoints
    path('export/orders/', ExportOrdersView.as_view(), name='export-orders'),
    path('export/inventory/', ExportInventoryView.as_view(), name='export-inventory'),
    path('export/jobs/', ExportJobsView.as_view(), name='export-jobs'),
    path('export/contacts/', ExportContactsView.as_view(), name='export-contacts'),
    path('export/customers/', ExportCustomersView.as_view(), name='export-customers'),
    path('export/absensi/', ExportAbsensiView.as_view(), name='export-absensi'),
    path('export/staff-performance/', ExportStaffPerformanceView.as_view(), name='export-staff-performance'),
    path('export/stock-movement/', ExportStockMovementView.as_view(), name='export-stock-movement'),
    path('export/products/', ExportProductsView.as_view(), name='export-products'),
    path('export/customer-notes/', ExportCustomerNotesView.as_view(), name='export-customer-notes'),
    path('export/cash-transactions/', ExportCashTransactionsView.as_view(), name='export-cash-transactions'),
    path('export/sales-items-by-brand/', ExportSalesItemsByBrandView.as_view(), name='export-sales-items-by-brand'),
    path('export/sales-details/', ExportSalesDetailsView.as_view(), name='export-sales-details'),
    path('stock-fifo/sync/', product_views.StockFifoSyncView.as_view(), name='stock-fifo-sync'),
    path('stock-fifo/status/', product_views.StockFifoStatusView.as_view(), name='stock-fifo-status'),

    # Reports Endpoints
    path('reports/staff-performance/', StaffPerformanceReportView.as_view(), name='staff-performance-report'),
    # Laporan generik — HARUS setelah route reports/ yang spesifik di atas,
    # karena <str:report_id> akan menangkap segmen apa pun.
    path('reports/<str:report_id>/export/', ReportExportView.as_view(), name='report-export'),
    path('reports/<str:report_id>/', ReportDataView.as_view(), name='report-data'),

    # Explicit restock URL — standalone APIView, tidak pakai @action ViewSet
    path('inventory/<str:pk>/restock/', InventoryRestockView.as_view(), name='inventory-restock'),
    
    # Webhook Bot WA (Fonnte)
    path('webhook/fonnte/', FonnteWebhookView.as_view(), name='webhook-fonnte'),

    # Webhook Bot WA (Evolution API)
    path('webhook/evolution/', EvolutionWebhookView.as_view(), name='webhook-evolution'),

    # Business Settings (mirip OrgSettings di Django CRM)
    path('business-settings/', BusinessSettingsView.as_view(), name='business-settings'),
    
    # WhatsApp Chat Integration
    path('whatsapp/status/', views.WhatsAppStatusView.as_view(), name='whatsapp-status'),
    path('whatsapp/chats/', views.WhatsAppChatsView.as_view(), name='whatsapp-chats'),
    path('whatsapp/messages/', views.WhatsAppMessagesView.as_view(), name='whatsapp-messages'),
    path('whatsapp/send/', views.WhatsAppSendMessageView.as_view(), name='whatsapp-send'),
    path('whatsapp/send-media/', views.WhatsAppSendMediaView.as_view(), name='whatsapp-send-media'),

    # Client Error Logging
    path('log-client-error/', views.ClientLogView.as_view(), name='log-client-error'),

    # Public upload design susulan
    path('public/get-order-details/', views.PublicOrderDetailsView.as_view(), name='public-get-order-details'),
    path('public/submit-design/', views.PublicSubmitDesignView.as_view(), name='public-submit-design'),

    # Health check
    path('health/', HealthCheckView.as_view(), name='health-check'),
]