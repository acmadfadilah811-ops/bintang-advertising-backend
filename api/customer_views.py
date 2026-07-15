import csv
import io

from django.contrib.auth.hashers import make_password
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from api.permissions import IsOwnerManagerAdminOrReadOnly

from .customer_models import (
    CustomerGroup, Customer, CustomerNote, CustomerNoteEntry, CustomerNoteDocument,
    CustomerNoteTag, CustomerReview, Supplier,
)
from .customer_serializers import (
    CustomerGroupSerializer, CustomerSerializer, CustomerNoteSerializer,
    CustomerNoteEntrySerializer, CustomerNoteDocumentSerializer, CustomerNoteTagSerializer,
    CustomerReviewSerializer, SupplierSerializer,
)

MAX_IMPORT_ROWS = 500
GENDER_IMPORT_MAP = {'M': 'L', 'F': 'P', 'L': 'L', 'P': 'P'}


def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'ya', 'yes')


def _parsed_date(value):
    value = (value or '').strip()
    return parse_date(value) if value else None


class ToggleStatusMixin:
    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        instance = self.get_object()
        instance.is_active = not instance.is_active
        instance.save()
        return Response(self.get_serializer(instance).data)


class CustomerGroupViewSet(ToggleStatusMixin, viewsets.ModelViewSet):
    """Tipe Pelanggan: Pelanggan & Supplier > Tipe Pelanggan."""
    queryset = CustomerGroup.objects.all()
    serializer_class = CustomerGroupSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)


class CustomerViewSet(ToggleStatusMixin, viewsets.ModelViewSet):
    """Pelanggan: Pelanggan & Supplier > Pelanggan."""
    queryset = Customer.objects.all().select_related('customer_group')
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        raw_password = serializer.validated_data.get('password')
        serializer.save(
            dibuat_oleh=self.request.user,
            password=make_password(raw_password) if raw_password else '',
        )

    def perform_update(self, serializer):
        raw_password = serializer.validated_data.get('password')
        if raw_password:
            serializer.save(password=make_password(raw_password))
        else:
            # Kosong/tidak dikirim → jangan timpa password yang sudah tersimpan.
            serializer.validated_data.pop('password', None)
            serializer.save()

    @action(detail=False, methods=['post'], url_path='import-csv', parser_classes=[MultiPartParser])
    def import_csv(self, request):
        """Import massal dari CSV (max. 500 baris), kolom mengikuti template olsera_customer_import_template."""
        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': 'File CSV tidak ditemukan.'}, status=400)

        try:
            decoded = upload.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response({'error': 'File harus berupa CSV dengan encoding UTF-8.'}, status=400)

        rows = list(csv.DictReader(io.StringIO(decoded)))
        if len(rows) > MAX_IMPORT_ROWS:
            return Response(
                {'error': f'Maksimal {MAX_IMPORT_ROWS} baris per import (file ini berisi {len(rows)} baris).'},
                status=400,
            )

        group_cache = {}
        created = 0
        row_errors = []

        for idx, row in enumerate(rows, start=2):  # baris 1 adalah header
            nama = (row.get('name') or '').strip()
            if not nama:
                row_errors.append({'row': idx, 'message': 'Kolom "name" wajib diisi.'})
                continue

            tipe = (row.get('customer_type') or '').strip()
            group = None
            if tipe:
                group = group_cache.get(tipe.lower())
                if group is None:
                    group, _ = CustomerGroup.objects.get_or_create(
                        nama=tipe, defaults={'dibuat_oleh': request.user}
                    )
                    group_cache[tipe.lower()] = group

            try:
                Customer.objects.create(
                    kode_pelanggan=(row.get('code') or '').strip(),
                    customer_group=group,
                    nama=nama,
                    email=(row.get('email') or '').strip(),
                    handphone=(row.get('phone') or '').strip(),
                    alamat=(row.get('address') or '').strip(),
                    kode_pos=(row.get('postal_code') or '').strip(),
                    jenis_kelamin=GENDER_IMPORT_MAP.get((row.get('gender') or '').strip().upper(), ''),
                    tanggal_lahir=_parsed_date(row.get('dob')),
                    tanggal_berakhir=_parsed_date(row.get('expiry_date')),
                    bekukan=_truthy(row.get('is_frozen')),
                    kota=(row.get('city') or '').strip(),
                    kecamatan=(row.get('subdistrict') or '').strip(),
                    nama_perusahaan=(row.get('company') or '').strip(),
                    terima_buletin=_truthy(row.get('accept_newsletter')),
                    batas_kredit=float(row.get('credit_limit') or 0),
                    loyalty_points=int(float(row.get('loyalty_points') or 0)),
                    catatan=(row.get('notes') or '').strip(),
                    dibuat_oleh=request.user,
                )
                created += 1
            except Exception as e:
                row_errors.append({'row': idx, 'message': str(e)})

        return Response({'created': created, 'total_rows': len(rows), 'errors': row_errors})


class CustomerNoteViewSet(viewsets.ModelViewSet):
    """Catatan Pelanggan: Pelanggan & Supplier > Catatan Pelanggan."""
    queryset = CustomerNote.objects.select_related('customer').prefetch_related('tags', 'entries', 'documents')
    serializer_class = CustomerNoteSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)


class CustomerNoteEntryViewSet(viewsets.ModelViewSet):
    """Entri catatan (log/riwayat berulang) di dalam satu CustomerNote. Filter: ?note=<id>."""
    queryset = CustomerNoteEntry.objects.all()
    serializer_class = CustomerNoteEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        note_id = self.request.query_params.get('note')
        if note_id:
            qs = qs.filter(note_id=note_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)


class CustomerNoteDocumentViewSet(viewsets.ModelViewSet):
    """Lampiran dokumen (gambar/PDF, maks. 5MB, maks. 5/catatan). Filter: ?note=<id>."""
    queryset = CustomerNoteDocument.objects.all()
    serializer_class = CustomerNoteDocumentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def get_queryset(self):
        qs = super().get_queryset()
        note_id = self.request.query_params.get('note')
        if note_id:
            qs = qs.filter(note_id=note_id)
        return qs


class CustomerNoteTagViewSet(viewsets.ModelViewSet):
    """Tag bebas untuk Catatan Pelanggan — create bersifat get-or-create (idempoten by nama)."""
    queryset = CustomerNoteTag.objects.all()
    serializer_class = CustomerNoteTagSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        nama = (request.data.get('nama') or '').strip()
        if not nama:
            return Response({'nama': ['Nama tag wajib diisi.']}, status=400)
        tag, _ = CustomerNoteTag.objects.get_or_create(nama=nama)
        return Response(self.get_serializer(tag).data, status=201)


class CustomerReviewViewSet(viewsets.ModelViewSet):
    """Ulasan Pelanggan: Pelanggan & Supplier > Ulasan Pelanggan."""
    queryset = CustomerReview.objects.all()
    serializer_class = CustomerReviewSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)


class SupplierViewSet(ToggleStatusMixin, viewsets.ModelViewSet):
    """Supplier: Pelanggan & Supplier > Supplier."""
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsOwnerManagerAdminOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(dibuat_oleh=self.request.user)
