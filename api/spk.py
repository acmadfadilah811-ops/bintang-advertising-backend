"""Penerbitan SPK produksi.

SPK bisa lahir dari dua sumber — item pesanan (alur order/antrean WA) dan item
transaksi POS (pesanan custom di terminal kasir). Logika penentuan tahap dan
pembuatan JobBoard-nya sama persis, jadi dikumpulkan di sini alih-alih
disalin ke dua view.

Dipakai oleh AssignOrderView (api/views/orders.py) dan POSSaleSpkView
(api/pos_views.py).
"""

import logging

from .models import CustomUser, JobBoard, TahapProses

logger = logging.getLogger(__name__)


class SpkError(Exception):
    """Kesalahan yang layak dikembalikan ke pengguna sebagai 400/404."""

    def __init__(self, pesan, status_code=400):
        super().__init__(pesan)
        self.pesan = pesan
        self.status_code = status_code


# Peran yang hanya boleh menerbitkan SPK ke antrean divisi, bukan menunjuk
# staff tertentu. Penugasan per-staff adalah wewenang kepala produksi/manager;
# kasir cukup melempar pekerjaan ke divisi dan divisi yang membagi tugas.
ROLE_TANPA_PENUGASAN_STAFF = ('kasir',)


def resolve_staff(staff_id, pemohon=None):
    """Ambil staff tujuan SPK.

    `pemohon` adalah user yang menerbitkan SPK. Untuk peran pada
    ROLE_TANPA_PENUGASAN_STAFF, penunjukan staff ditolak — bukan diabaikan
    diam-diam — supaya kesalahan pemakaian API terlihat jelas.
    """
    if not staff_id:
        return None
    if pemohon is not None and getattr(pemohon, 'role', None) in ROLE_TANPA_PENUGASAN_STAFF:
        raise SpkError(
            'Akun kasir hanya dapat menerbitkan SPK ke divisi. '
            'Penunjukan staff dilakukan oleh kepala divisi atau manager.',
            403,
        )
    try:
        return CustomUser.objects.get(pk=staff_id, role='staff')
    except CustomUser.DoesNotExist:
        raise SpkError('Staff tidak ditemukan.', 404)


def resolve_tahap(tahap_id=None, divisi_id=None, staff=None):
    """Tentukan tahap produksi, dengan urutan fallback yang sama seperti
    penerbitan SPK dari antrean WA: tahap eksplisit -> tahap pertama divisi ->
    tahap pertama divisi milik staff.
    """
    tahap = None

    if tahap_id:
        try:
            tahap = TahapProses.objects.get(pk=tahap_id)
        except TahapProses.DoesNotExist:
            logger.warning('TahapProses %s tidak ditemukan saat menerbitkan SPK.', tahap_id)

    if not tahap and divisi_id:
        tahap = TahapProses.objects.filter(divisi_id=divisi_id).order_by('urutan').first()
        if not tahap:
            from .models import Divisi
            divisi = Divisi.objects.filter(pk=divisi_id).first()
            if divisi:
                tahap = TahapProses.objects.create(
                    divisi=divisi,
                    nama=divisi.nama,
                    urutan=1
                )
                logger.info("Otomatis membuat TahapProses '%s' untuk Divisi ID %s", divisi.nama, divisi_id)

    if not tahap and staff and staff.divisi_id:
        tahap = TahapProses.objects.filter(divisi=staff.divisi).order_by('urutan').first()
        if not tahap:
            tahap = TahapProses.objects.create(
                divisi=staff.divisi,
                nama=staff.divisi.nama,
                urutan=1
            )
            logger.info("Otomatis membuat TahapProses '%s' untuk Divisi staff", staff.divisi.nama)

    return tahap


def terbitkan(items, *, field, tahap, staff, biaya_desain=0, insentif=0):
    """Buat/perbarui JobBoard untuk tiap item.

    `field` menentukan sumbernya: 'order_item' atau 'pos_sale_item'. Lookup
    memakai (item, tahap) supaya menerbitkan ulang ke tahap berbeda tidak
    menimpa SPK tahap sebelumnya.
    """
    if field not in ('order_item', 'pos_sale_item'):
        raise ValueError(f"field SPK tidak dikenal: {field}")

    if not tahap and not staff:
        raise SpkError('staff_id atau divisi/tahap wajib diisi untuk penerbitan SPK.')

    items = list(items)
    if not items:
        raise SpkError('Tidak ada item yang bisa diterbitkan SPK-nya.')

    dibuat = []
    for item in items:
        job, created = JobBoard.objects.update_or_create(
            **{field: item},
            tahap=tahap,
            defaults={
                'pic_staff': staff,
                'status_pekerjaan': 'antrean',
                'biaya_desain': biaya_desain,
                'insentif': insentif,
                'waktu_mulai': None,
                'waktu_selesai': None,
            },
        )
        dibuat.append({'job_id': job.id, 'item': job.nama_produk, 'created': created})
    return dibuat


def nama_target(staff, tahap):
    if staff:
        return staff.username
    if tahap and tahap.divisi:
        return tahap.divisi.nama
    return 'Divisi'


def is_divisi_desain(staff, tahap):
    """Order yang masuk divisi desain berstatus 'desain', sisanya 'proses'."""
    if tahap and tahap.divisi and tahap.divisi.nama.lower() == 'desain':
        return True
    if staff and staff.divisi and staff.divisi.nama.lower() == 'desain':
        return True
    return False
