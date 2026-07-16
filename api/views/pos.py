from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q

from ..models import POSAntrianDevice, SaldoKasHarian, RingkasanShift
from ..serializers import POSAntrianDeviceSerializer, SaldoKasHarianSerializer, RingkasanShiftSerializer
from ..permissions import IsOwnerManagerAdminOrReadOnly, IsOwnerManagerAdminOrKasir

class POSAntrianDeviceViewSet(viewsets.ModelViewSet):
    queryset = POSAntrianDevice.objects.all().order_by('id')
    serializer_class = POSAntrianDeviceSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]


class SaldoKasHarianViewSet(viewsets.ModelViewSet):
    queryset = SaldoKasHarian.objects.all().order_by('-tanggal', '-id')
    serializer_class = SaldoKasHarianSerializer
    permission_classes = [IsAuthenticated, IsOwnerManagerAdminOrKasir]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.role == 'kasir':
            queryset = queryset.filter(kasir=self.request.user)
        tanggal = self.request.query_params.get('tanggal')
        if tanggal:
            queryset = queryset.filter(tanggal=tanggal)
        
        query = self.request.query_params.get('query')
        if query:
            queryset = queryset.filter(
                Q(kasir__username__icontains=query) |
                Q(kasir__first_name__icontains=query) |
                Q(kasir__last_name__icontains=query) |
                Q(shift__icontains=query)
            )
        return queryset


class RingkasanShiftViewSet(viewsets.ModelViewSet):
    queryset = RingkasanShift.objects.all().order_by('-tanggal', '-mulai')
    serializer_class = RingkasanShiftSerializer
    permission_classes = [IsAuthenticated, IsOwnerManagerAdminOrKasir]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.role == 'kasir':
            queryset = queryset.filter(kasir=self.request.user)
        
        tanggal_mulai = self.request.query_params.get('tanggal_mulai')
        tanggal_akhir = self.request.query_params.get('tanggal_akhir')
        
        if tanggal_mulai:
            queryset = queryset.filter(tanggal__gte=tanggal_mulai)
        if tanggal_akhir:
            queryset = queryset.filter(tanggal__lte=tanggal_akhir)
            
        query = self.request.query_params.get('query')
        if query:
            queryset = queryset.filter(
                Q(kasir__username__icontains=query) |
                Q(kasir__first_name__icontains=query) |
                Q(kasir__last_name__icontains=query)
            )
        return queryset
