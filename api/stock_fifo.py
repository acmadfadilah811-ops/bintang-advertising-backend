"""Layanan lapisan biaya stok (FIFO / FEFO).

Satu-satunya tempat logika FIFO berada. Semua alur mutasi stok (stok masuk,
pembelian, produksi, stok keluar, retur, penjualan POS, opname) memanggil
fungsi di modul ini supaya perilakunya konsisten.

Prinsip: lapisan dipelihara di SEMUA mode stok. `get_stock_system()` hanya
menentukan urutan konsumsi (FIFO vs FEFO) dan apakah HPP dipakai dari lapisan.
"""
import logging
from decimal import Decimal

from django.db.models import Sum

from .models import SystemConfig
from .product_models import (
    Product, ProductVariant, StockLayer, StockLayerConsumption,
)

logger = logging.getLogger(__name__)

STOCK_SYSTEM_KEY = 'stock_system'
MODE_AVERAGE = 'average'
MODE_FIFO = 'fifo'
MODE_FIFO_EXPIRED = 'fifo_expired'
VALID_MODES = (MODE_AVERAGE, MODE_FIFO, MODE_FIFO_EXPIRED)

ZERO = Decimal('0')


def _dec(v):
    """Ubah apa pun jadi Decimal dengan aman (hindari campur float/Decimal)."""
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def get_stock_system():
    """Mode stok aktif: 'average' (default), 'fifo', atau 'fifo_expired'."""
    try:
        cfg = SystemConfig.objects.filter(key=STOCK_SYSTEM_KEY).first()
    except Exception:  # tabel belum ada saat migrasi awal
        return MODE_AVERAGE
    val = (cfg.value if cfg else '') or MODE_AVERAGE
    return val if val in VALID_MODES else MODE_AVERAGE


def is_fifo_active(mode=None):
    return (mode or get_stock_system()) != MODE_AVERAGE


# ---------------------------------------------------------------------------
# Stok masuk
# ---------------------------------------------------------------------------
def create_layer(product, variant, qty, harga_beli, tanggal,
                 sumber_tipe='manual', sumber_nomor='', rak='', tanggal_kadaluwarsa=None):
    """Catat satu lapisan biaya baru. Mengembalikan StockLayer, atau None bila qty <= 0."""
    qty = _dec(qty)
    if qty <= 0:
        return None
    return StockLayer.objects.create(
        product=product,
        variant=variant,
        tanggal_masuk=tanggal,
        qty_masuk=qty,
        sisa_qty=qty,
        harga_beli=_dec(harga_beli),
        tanggal_kadaluwarsa=tanggal_kadaluwarsa,
        rak=rak or '',
        sumber_tipe=sumber_tipe,
        sumber_nomor=sumber_nomor or '',
    )


# ---------------------------------------------------------------------------
# Stok keluar
# ---------------------------------------------------------------------------
def _open_layers(product, variant, mode):
    """Lapisan yang masih punya sisa, terurut sesuai mode.

    FIFO  : tanggal_masuk, id
    FEFO  : tanggal_kadaluwarsa lebih dulu (yang tanpa tanggal ditaruh paling akhir),
            lalu tanggal_masuk, id
    """
    qs = (StockLayer.objects
          .select_for_update()
          .filter(product=product, variant=variant, sisa_qty__gt=0))
    if mode == MODE_FIFO_EXPIRED:
        # `F(...).asc(nulls_last=True)` supaya lapisan tanpa kedaluwarsa tidak
        # ikut diprioritaskan.
        from django.db.models import F
        return qs.order_by(F('tanggal_kadaluwarsa').asc(nulls_last=True), 'tanggal_masuk', 'id')
    return qs.order_by('tanggal_masuk', 'id')


def consume_layers(product, variant, qty, movement=None, mode=None):
    """Ambil `qty` dari lapisan (tertua/paling cepat kedaluwarsa dulu).

    Mengembalikan (hpp_total: Decimal, rincian: list[StockLayerConsumption]).

    Bila lapisan tidak mencukupi, sisanya tetap dicatat sebagai konsumsi
    `is_shortfall=True` dengan harga beli produk sebagai fallback, dan ditulis
    ke log — supaya selisih data terlihat, bukan hilang diam-diam.
    """
    qty = _dec(qty)
    if qty <= 0:
        return ZERO, []

    mode = mode or get_stock_system()
    sisa_diminta = qty
    hpp_total = ZERO
    rincian = []

    for layer in _open_layers(product, variant, mode):
        if sisa_diminta <= 0:
            break
        ambil = min(layer.sisa_qty, sisa_diminta)
        if ambil <= 0:
            continue
        layer.sisa_qty = layer.sisa_qty - ambil
        layer.save(update_fields=['sisa_qty'])

        rincian.append(StockLayerConsumption.objects.create(
            layer=layer, movement=movement, product=product, variant=variant,
            qty=ambil, harga_beli=layer.harga_beli, is_shortfall=False,
        ))
        hpp_total += ambil * layer.harga_beli
        sisa_diminta -= ambil

    if sisa_diminta > 0:
        # Lapisan habis tapi permintaan belum terpenuhi.
        fallback = _dec(getattr(variant or product, 'harga_beli', None) or product.harga_beli)
        rincian.append(StockLayerConsumption.objects.create(
            layer=None, movement=movement, product=product, variant=variant,
            qty=sisa_diminta, harga_beli=fallback, is_shortfall=True,
        ))
        hpp_total += sisa_diminta * fallback
        logger.warning(
            "[stock_fifo] Lapisan tidak cukup untuk %s (varian=%s): kurang %s unit. "
            "Memakai harga beli produk (%s) sebagai fallback.",
            product.nama, getattr(variant, 'id', None), sisa_diminta, fallback,
        )

    if movement is not None:
        movement.hpp_total = hpp_total
        movement.save(update_fields=['hpp_total'])

    return hpp_total, rincian


# ---------------------------------------------------------------------------
# Opname (set absolut)
# ---------------------------------------------------------------------------
def recalibrate_layers(product, variant, qty_fisik, tanggal,
                       harga_beli=None, sumber_nomor='', mode=None):
    """Selaraskan lapisan dengan hasil hitung fisik opname.

    Fisik < lapisan  -> konsumsi selisih (FIFO/FEFO).
    Fisik > lapisan  -> buat lapisan penyesuaian sebesar kelebihannya.
    """
    qty_fisik = _dec(qty_fisik)
    total_lapisan = layer_balance(product, variant)
    selisih = qty_fisik - total_lapisan

    if selisih == 0:
        return ZERO, []
    if selisih < 0:
        return consume_layers(product, variant, -selisih, movement=None, mode=mode)

    layer = create_layer(
        product, variant, selisih,
        harga_beli if harga_beli is not None else product.harga_beli,
        tanggal, sumber_tipe='opname', sumber_nomor=sumber_nomor,
    )
    return ZERO, [layer] if layer else []


# ---------------------------------------------------------------------------
# Utilitas
# ---------------------------------------------------------------------------
def layer_balance(product, variant=None):
    """Total sisa lapisan untuk satu produk/varian."""
    agg = (StockLayer.objects
           .filter(product=product, variant=variant)
           .aggregate(t=Sum('sisa_qty')))
    return _dec(agg['t'])


def sync_opening_layers():
    """Tombol "Sync Stok Produk": buat SATU lapisan saldo awal untuk tiap
    produk/varian yang punya stok tapi belum punya lapisan sama sekali.

    Idempoten — dijalankan berulang tidak menggandakan lapisan.
    """
    from django.utils import timezone
    hari_ini = timezone.now().date()
    dibuat = 0

    # Varian yang melacak stok sendiri.
    for v in ProductVariant.objects.select_related('product').all():
        if _dec(v.qty_stok) <= 0:
            continue
        if StockLayer.objects.filter(product=v.product, variant=v).exists():
            continue
        if create_layer(v.product, v, v.qty_stok, v.harga_beli or v.product.harga_beli,
                        hari_ini, sumber_tipe='saldo_awal', sumber_nomor='SYNC'):
            dibuat += 1

    # Produk tanpa varian.
    for p in Product.objects.all():
        if p.variants.exists():
            continue
        if _dec(p.qty_stok) <= 0:
            continue
        if StockLayer.objects.filter(product=p, variant=None).exists():
            continue
        if create_layer(p, None, p.qty_stok, p.harga_beli, hari_ini,
                        sumber_tipe='saldo_awal', sumber_nomor='SYNC'):
            dibuat += 1

    return dibuat


def reconciliation_report():
    """Cek integritas: qty_stok harus sama dengan total sisa lapisan.

    Dipakai endpoint status dan laporan `banding-fifo`.
    """
    tanpa_lapisan = 0
    tidak_cocok = []

    for p in Product.objects.prefetch_related('variants'):
        varian = list(p.variants.all())
        if varian:
            for v in varian:
                saldo = layer_balance(p, v)
                if saldo == 0 and _dec(v.qty_stok) > 0:
                    tanpa_lapisan += 1
                if saldo != _dec(v.qty_stok):
                    tidak_cocok.append({
                        'produk': f"{p.nama} - {v.nama_varian}",
                        'qty_stok': float(_dec(v.qty_stok)),
                        'stok_fifo': float(saldo),
                    })
        else:
            saldo = layer_balance(p, None)
            if saldo == 0 and _dec(p.qty_stok) > 0:
                tanpa_lapisan += 1
            if saldo != _dec(p.qty_stok):
                tidak_cocok.append({
                    'produk': p.nama,
                    'qty_stok': float(_dec(p.qty_stok)),
                    'stok_fifo': float(saldo),
                })

    return {'tanpa_lapisan': tanpa_lapisan, 'tidak_cocok': tidak_cocok}
