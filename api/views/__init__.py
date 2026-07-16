from .legacy import *
from .whatsapp import (
    FonnteWebhookView, EvolutionWebhookView, WhatsAppStatusView,
    WhatsAppChatsView, WhatsAppMessagesView, WhatsAppSendMessageView,
    WhatsAppSendMediaView
)
from .orders import (
    OrderViewSet, AssignOrderView, OrderItemViewSet, ForwardJobView
)
from .jobs import (
    JobBoardViewSet, JobMaterialDeductView, deduct_job_materials_if_needed
)
from .inventory import (
    InventoryItemViewSet, InventoryRestockView, record_material_consumption_to_general_ledger
)
from .contacts import (
    ContactViewSet, ContactStatsView
)
from .config import (
    SystemConfigViewSet, FAQViewSet, BusinessSettingsView
)
from .dashboard import (
    DashboardView
)
from .users import (
    CustomUserViewSet, CreateUserView
)







