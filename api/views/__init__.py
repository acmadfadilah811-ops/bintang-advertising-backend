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


