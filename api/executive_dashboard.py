"""Agregasi dashboard eksekutif.

Prinsip modul ini: HANYA menyajikan angka yang benar-benar terhitung dari data
yang ada. Metrik yang butuh buku besar (laba bersih, saldo kas, rasio lancar/
cepat) sengaja TIDAK disajikan, bukan ditaksir dengan persentase tetap — angka
karangan pada dashboard manajemen lebih berbahaya daripada angka yang absen.
Daftar yang belum tersedia dikembalikan lewat `unavailable` supaya UI bisa
menjelaskannya ke pengguna.

Berbeda dari api/views/dashboard.py yang bersifat operasional harian, modul ini
memberi ringkasan lintas periode untuk manajemen.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Order
from .pos_models import POSSale, POSSaleItem
from .product_models import Product, ProductStockMovement, StockProductionDocument

ZERO = Decimal('0')

# Metrik yang sengaja tidak ditampilkan, beserta alasannya. Ditampilkan apa
# adanya di UI supaya manajemen tahu apa yang BELUM tercakup.
UNAVAILABLE = [
    {'label': 'Laba bersih', 'reason': 'Butuh beban operasional dari buku besar; jurnal akuntansi belum aktif.'},
    {'label': 'Kas & bank', 'reason': 'Butuh saldo akun kas dari buku besar.'},
    {'label': 'Rasio lancar & cepat', 'reason': 'Butuh neraca lengkap (aset & liabilitas lancar).'},
]


def _periode(period):
    """Rentang periode berjalan + rentang pembanding dengan panjang yang sama."""
    hari_ini = timezone.localdate()
    if period == 'mtd':
        mulai = hari_ini.replace(day=1)
    elif period == 'qtd':
        mulai = date(hari_ini.year, ((hari_ini.month - 1) // 3) * 3 + 1, 1)
    elif period == '12m':
        mulai = hari_ini - timedelta(days=364)
    else:  # ytd
        mulai = date(hari_ini.year, 1, 1)

    panjang = (hari_ini - mulai).days + 1
    sebelum_akhir = mulai - timedelta(days=1)
    return mulai, hari_ini, sebelum_akhir - timedelta(days=panjang - 1), sebelum_akhir


def _pendapatan(mulai, akhir):
    """Pendapatan = POS lunas + order, keduanya dalam rentang tanggal."""
    pos = POSSale.objects.filter(
        status='paid', created_at__date__gte=mulai, created_at__date__lte=akhir
    ).aggregate(v=Sum('total'))['v'] or ZERO
    order = Order.objects.filter(
        waktu__date__gte=mulai, waktu__date__lte=akhir
    ).exclude(status_global='batal').aggregate(v=Sum('total_harga'))['v'] or ZERO
    return pos + order


def _hpp(mulai, akhir):
    """HPP nyata dari lapisan FIFO yang dikonsumsi (bukan taksiran).

    Pengembalian barang mengurangi HPP, karena stoknya kembali masuk.
    """
    keluar = ProductStockMovement.objects.filter(
        tipe='penjualan', created_at__date__gte=mulai, created_at__date__lte=akhir
    ).aggregate(v=Sum('hpp_total'))['v'] or ZERO
    kembali = ProductStockMovement.objects.filter(
        tipe='pengembalian', created_at__date__gte=mulai, created_at__date__lte=akhir
    ).aggregate(v=Sum('hpp_total'))['v'] or ZERO
    return keluar - kembali


def _delta(sekarang, sebelum):
    """Perubahan persen. None bila tidak ada pembanding — UI menyembunyikannya."""
    if not sebelum:
        return None
    return round(float((sekarang - sebelum) / abs(sebelum) * 100), 1)


def _tren(mulai, akhir):
    """Tren bulanan pendapatan POS dan HPP nyata."""
    pendapatan = {
        row['bulan'].date(): row['v'] or ZERO
        for row in POSSale.objects.filter(
            status='paid', created_at__date__gte=mulai, created_at__date__lte=akhir
        ).annotate(bulan=TruncMonth('created_at')).values('bulan').annotate(v=Sum('total'))
    }
    hpp = {
        row['bulan'].date(): row['v'] or ZERO
        for row in ProductStockMovement.objects.filter(
            tipe='penjualan', created_at__date__gte=mulai, created_at__date__lte=akhir
        ).annotate(bulan=TruncMonth('created_at')).values('bulan').annotate(v=Sum('hpp_total'))
    }
    hasil = []
    for bulan in sorted(set(pendapatan) | set(hpp)):
        nilai = pendapatan.get(bulan, ZERO)
        modal = hpp.get(bulan, ZERO)
        hasil.append({
            'periode': bulan.strftime('%b %Y'),
            'pendapatan': float(nilai),
            'hpp': float(modal),
            'laba_kotor': float(nilai - modal),
        })
    return hasil


def _produk_terlaris(mulai, akhir, batas=5):
    rows = POSSaleItem.objects.filter(
        sale__status='paid', sale__created_at__date__gte=mulai, sale__created_at__date__lte=akhir
    ).values('nama_snapshot').annotate(
        qty=Sum('qty'), nilai=Sum('subtotal')
    ).order_by('-nilai')[:batas]
    return [
        {'nama': r['nama_snapshot'], 'qty': float(r['qty'] or 0), 'nilai': float(r['nilai'] or 0)}
        for r in rows
    ]


def _stok():
    aktif = Product.objects.filter(is_active=True)
    dilacak = aktif.filter(lacak_inventori=True)
    habis = dilacak.filter(qty_stok__lte=0).count()
    menipis = dilacak.filter(qty_stok__gt=0, qty_stok__lte=F('stok_minimum')).count()
    total = dilacak.count()
    nilai = aktif.aggregate(v=Sum(ExpressionWrapper(
        F('qty_stok') * F('harga_beli'), output_field=DecimalField(max_digits=20, decimal_places=2)
    )))['v'] or ZERO
    return {
        'nilai_persediaan': float(nilai),
        'total_dilacak': total,
        'habis': habis,
        'menipis': menipis,
        'sehat': max(0, total - habis - menipis),
    }


def build(period='ytd'):
    mulai, akhir, sebelum_mulai, sebelum_akhir = _periode(period)

    pendapatan = _pendapatan(mulai, akhir)
    hpp = _hpp(mulai, akhir)
    pendapatan_lalu = _pendapatan(sebelum_mulai, sebelum_akhir)
    hpp_lalu = _hpp(sebelum_mulai, sebelum_akhir)

    transaksi = POSSale.objects.filter(
        status='paid', created_at__date__gte=mulai, created_at__date__lte=akhir
    ).count()
    # Piutang bersifat posisi (bukan periode): seluruh sisa tagihan yang belum lunas.
    piutang = Order.objects.filter(sisa_tagihan__gt=0).aggregate(v=Sum('sisa_tagihan'))['v'] or ZERO
    stok = _stok()

    produksi_total = StockProductionDocument.objects.filter(tanggal__range=(mulai, akhir)).count()
    produksi_selesai = StockProductionDocument.objects.filter(
        tanggal__range=(mulai, akhir), status='selesai'
    ).count()

    return {
        'generated_at': timezone.now().isoformat(),
        'periode': {
            'kode': period,
            'mulai': mulai.isoformat(),
            'akhir': akhir.isoformat(),
            'label': f'{mulai:%d %b %Y} – {akhir:%d %b %Y}',
            'pembanding': f'{sebelum_mulai:%d %b %Y} – {sebelum_akhir:%d %b %Y}',
        },
        'kpi': [
            {'key': 'pendapatan', 'label': 'Pendapatan', 'value': float(pendapatan),
             'delta': _delta(pendapatan, pendapatan_lalu)},
            {'key': 'hpp', 'label': 'HPP (FIFO)', 'value': float(hpp),
             'delta': _delta(hpp, hpp_lalu)},
            {'key': 'laba_kotor', 'label': 'Laba Kotor', 'value': float(pendapatan - hpp),
             'delta': _delta(pendapatan - hpp, pendapatan_lalu - hpp_lalu)},
            {'key': 'piutang', 'label': 'Piutang Berjalan', 'value': float(piutang), 'delta': None},
            {'key': 'persediaan', 'label': 'Nilai Persediaan', 'value': stok['nilai_persediaan'],
             'delta': None},
            {'key': 'transaksi', 'label': 'Transaksi POS', 'value': transaksi, 'delta': None,
             'format': 'angka'},
        ],
        'tren': _tren(mulai, akhir),
        'produk_terlaris': _produk_terlaris(mulai, akhir),
        'stok': stok,
        'produksi': {'total': produksi_total, 'selesai': produksi_selesai},
        'unavailable': UNAVAILABLE,
    }
