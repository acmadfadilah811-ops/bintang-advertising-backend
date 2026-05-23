from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AkunViewSet, BukuBesarView, TransaksiBukuBesarViewSet

router = DefaultRouter()
router.register(r'akun', AkunViewSet, basename='akun')
router.register(r'transaksi', TransaksiBukuBesarViewSet, basename='transaksi')

urlpatterns = [
    path('', include(router.urls)),
    path('buku-besar/', BukuBesarView.as_view(), name='buku-besar'),
]
