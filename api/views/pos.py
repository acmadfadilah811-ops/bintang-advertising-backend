from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q, Sum
from django.utils import timezone

from ..models import POSAntrianDevice, SaldoKasHarian, RingkasanShift, POSPaymentMethod
from ..serializers import (
    POSAntrianDeviceSerializer, SaldoKasHarianSerializer, RingkasanShiftSerializer,
    POSPaymentMethodSerializer,
)
from ..permissions import IsOwnerManagerAdminOrReadOnly, IsOwnerManagerAdminOrKasir


class POSPaymentMethodViewSet(viewsets.ModelViewSet):
    """Cara pembayaran POS (Pengaturan POS > Pembayaran)."""
    queryset = POSPaymentMethod.objects.all()
    serializer_class = POSPaymentMethodSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

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

    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        """Tutup shift sekaligus membuat Ringkasan Shift (V2).

        expected = kas awal
                 + penjualan POS tunai selama shift
                 + Pendapatan - Pengeluaran (kas masuk/keluar) pada jendela shift.

        Dihitung di server agar angka rekonsiliasi tidak bisa dimanipulasi dari
        browser. `selisih` dihitung otomatis oleh RingkasanShift.save().
        """
        from ..pos_models import POSSale
        from ..finance_models import CashTransaction

        shift = self.get_object()
        if shift.waktu_tutup:
            return Response({'error': 'Shift ini sudah ditutup.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            kas_akhir = float(request.data.get('kas_akhir') or 0)
        except (TypeError, ValueError):
            return Response({'error': 'kas_akhir harus berupa angka.'}, status=status.HTTP_400_BAD_REQUEST)

        sekarang = timezone.now()
        mulai = shift.waktu_buka or sekarang

        # Metode yang dihitung sebagai tunai: yang bertipe 'Tunai' di master
        # metode pembayaran, plus nama bawaan 'cash'/'tunai'.
        nama_tunai = {
            (n or '').lower()
            for n in POSPaymentMethod.objects.filter(tipe='Tunai').values_list('nama', flat=True)
        }
        nama_tunai |= {'cash', 'tunai'}

        tunai = 0.0
        for s in POSSale.objects.filter(shift=shift, status='paid'):
            if (s.metode_bayar or '').lower() in nama_tunai:
                tunai += float(s.total or 0)

        tx = CashTransaction.objects.filter(waktu__gte=mulai, waktu__lte=sekarang)
        masuk = float(tx.filter(arah='pendapatan').aggregate(t=Sum('jumlah'))['t'] or 0)
        keluar = float(tx.filter(arah='pengeluaran').aggregate(t=Sum('jumlah'))['t'] or 0)

        kas_awal = float(shift.kas_awal or 0)
        expected = kas_awal + tunai + masuk - keluar

        shift.kas_akhir = kas_akhir
        shift.waktu_tutup = sekarang
        shift.catatan = (request.data.get('catatan') or '').strip()
        shift.save()

        ringkasan = RingkasanShift.objects.create(
            tanggal=shift.tanggal, kasir=shift.kasir,
            mulai=mulai, berakhir=sekarang,
            expected=expected, aktual=kas_akhir,
        )

        return Response({
            'shift': SaldoKasHarianSerializer(shift).data,
            'ringkasan': RingkasanShiftSerializer(ringkasan).data,
            'rincian': {
                'kas_awal': kas_awal,
                'penjualan_tunai': tunai,
                'kas_masuk': masuk,
                'kas_keluar': keluar,
                'expected': expected,
                'aktual': kas_akhir,
                'selisih': ringkasan.selisih,
            },
        }, status=status.HTTP_201_CREATED)


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
