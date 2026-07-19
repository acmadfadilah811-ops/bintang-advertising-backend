import csv
import io

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .finance_models import CashTransactionType, CashTransaction, CashTransactionAttachment
from .finance_serializers import (
    CashTransactionTypeSerializer, CashTransactionSerializer,
)

CSV_IMPORT_MAX_ROWS = 500


def _next_number(prefix):
    """Nomor dokumen berikutnya untuk prefix hari ini (ambil tertinggi, bukan count)."""
    last = CashTransaction.objects.filter(nomor__startswith=prefix).order_by('-nomor').first()
    if last:
        try:
            next_num = int(last.nomor[len(prefix):]) + 1
        except ValueError:
            next_num = 1
    else:
        next_num = 1
    return f"{prefix}{next_num:06d}"


def _norm_direction(raw):
    """Normalisasi teks Type CSV -> 'pendapatan' / 'pengeluaran'."""
    v = (raw or '').strip().lower()
    if v in ('pendapatan', 'income', 'masuk', 'kas masuk'):
        return 'pendapatan'
    if v in ('pengeluaran', 'expense', 'keluar', 'kas keluar'):
        return 'pengeluaran'
    return None


class CashTransactionTypeViewSet(viewsets.ModelViewSet):
    """Tipe Transaksi (master) untuk Pendapatan/Pengeluaran."""
    queryset = CashTransactionType.objects.all()
    serializer_class = CashTransactionTypeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        tipe = self.request.query_params.get('tipe')
        if tipe in ('pendapatan', 'pengeluaran'):
            qs = qs.filter(tipe=tipe)
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(nama__icontains=search)
        return qs

    def create(self, request, *args, **kwargs):
        """Hormati Pengaturan POS: 'Tidak diperbolehkan menambah tipe Kas
        Masuk/Keluar' — memblokir kasir/staff, pemilik & manajer tetap boleh."""
        from . import pos_settings
        user = request.user
        is_atasan = getattr(user, 'role', '') in ('owner', 'manager') or user.is_superuser
        if pos_settings.blokir_tambah_tipe_kas() and not is_atasan:
            return Response(
                {'error': 'Menambah tipe transaksi dinonaktifkan di Pengaturan POS. '
                          'Hubungi pemilik/manajer.'},
                status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)

    @action(detail=False, methods=['post'], url_path='import-csv')
    def import_csv(self, request):
        """Import massal tipe transaksi. Kolom: Name, Type (Pendapatan/Pengeluaran)."""
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'File CSV wajib diunggah.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            decoded = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV berformat teks (UTF-8).'}, status=status.HTTP_400_BAD_REQUEST)

        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > CSV_IMPORT_MAX_ROWS:
            return Response(
                {'error': f'Maksimal {CSV_IMPORT_MAX_ROWS} baris per import — file ini berisi {len(rows)} baris.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []
        for idx, row in enumerate(rows, start=2):  # baris 1 = header
            low = {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
            nama = low.get('name') or low.get('nama')
            tipe = _norm_direction(low.get('type') or low.get('tipe'))
            if not nama:
                errors.append(f"Baris {idx}: kolom Name kosong.")
                continue
            if not tipe:
                errors.append(f"Baris {idx}: Type '{low.get('type') or low.get('tipe') or ''}' tidak valid (isi Pendapatan/Pengeluaran).")
                continue
            obj = CashTransactionType.objects.create(nama=nama, tipe=tipe, dibuat_oleh=request.user)
            created.append(obj)

        return Response(
            {
                'created': CashTransactionTypeSerializer(created, many=True).data,
                'errors': errors,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST,
        )


class CashTransactionViewSet(viewsets.ModelViewSet):
    """Transaksi Pendapatan/Pengeluaran (Kas Masuk/Keluar) + lampiran bukti."""
    queryset = (
        CashTransaction.objects.all()
        .select_related('tipe_transaksi', 'staff', 'dibuat_oleh')
        .prefetch_related('lampiran')
    )
    serializer_class = CashTransactionSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        qs = super().get_queryset()
        arah = self.request.query_params.get('arah')
        if arah in ('pendapatan', 'pengeluaran'):
            qs = qs.filter(arah=arah)
        return qs

    def perform_create(self, serializer):
        tipe = serializer.validated_data.get('tipe_transaksi')
        nomor = _next_number(f"KAS{timezone.now().date().strftime('%y%m%d')}")
        staff = serializer.validated_data.get('staff') or self.request.user
        obj = serializer.save(nomor=nomor, arah=tipe.tipe, staff=staff, dibuat_oleh=self.request.user)
        for f in self.request.FILES.getlist('lampiran'):
            CashTransactionAttachment.objects.create(transaction=obj, file=f)

    def perform_update(self, serializer):
        tipe = serializer.validated_data.get('tipe_transaksi')
        obj = serializer.save()
        if tipe:
            obj.arah = tipe.tipe
            obj.save(update_fields=['arah', 'updated_at'])
        for f in self.request.FILES.getlist('lampiran'):
            CashTransactionAttachment.objects.create(transaction=obj, file=f)

    @action(detail=True, methods=['post'], url_path='remove-attachment')
    def remove_attachment(self, request, pk=None):
        tx = self.get_object()
        att_id = request.data.get('attachment_id')
        deleted, _ = CashTransactionAttachment.objects.filter(transaction=tx, id=att_id).delete()
        if not deleted:
            return Response({'error': 'Lampiran tidak ditemukan.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(CashTransactionSerializer(tx, context={'request': request}).data)
