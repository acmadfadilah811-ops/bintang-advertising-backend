"""Konversi satuan alternatif (UOM / Multi Satuan).

Aturan pokok: **stok & harga selalu disimpan dalam satuan DASAR** produk
(`Product.satuan`). Satuan alternatif hanya cara memasukkan/menampilkan angka,
dengan `konverter` = berapa satuan dasar per 1 satuan alternatif.

Contoh: produk satuan dasar 'pcs', satuan 'Dus' konverter 12.
Beli 2 Dus @ Rp 120.000  ->  qty dasar 24 pcs, harga dasar Rp 10.000/pcs.

Menyimpan basis dasar membuat seluruh logika stok, FIFO, dan laporan yang sudah
ada tetap benar tanpa perubahan.
"""
from decimal import Decimal

from .models import SystemConfig

UOM_ENABLED_KEY = 'uom_multi_enabled'
SATU = Decimal('1')


def _dec(v, default=SATU):
    if v is None or v == '':
        return default
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return default


def is_uom_enabled():
    """Multi Satuan/UOM aktif secara global?"""
    try:
        cfg = SystemConfig.objects.filter(key=UOM_ENABLED_KEY).first()
    except Exception:
        return False
    return str(cfg.value).lower() in ('true', '1', 'ya') if cfg else False


def get_units(product, variant=None):
    """Daftar satuan alternatif produk yang relevan untuk varian tertentu.

    `variant_id` pada unit boleh 'all' (berlaku untuk semua varian).
    """
    units = product.uom_units if isinstance(product.uom_units, list) else []
    hasil = []
    for u in units:
        if not isinstance(u, dict):
            continue
        vid = str(u.get('variant_id', 'all'))
        if variant is not None and vid not in ('all', str(variant.id)):
            continue
        hasil.append(u)
    return hasil


def find_unit(product, kode, variant=None):
    """Cari satuan berdasarkan kode (atau id). None bila tidak ketemu/kosong."""
    if not kode:
        return None
    kode_norm = str(kode).strip().lower()
    for u in get_units(product, variant):
        if str(u.get('kode_satuan', '')).strip().lower() == kode_norm:
            return u
        if str(u.get('id', '')) == str(kode):
            return u
    return None


def konverter(unit):
    """Faktor konversi satuan; minimal 1 supaya tidak pernah membagi nol."""
    k = _dec((unit or {}).get('konverter'), SATU)
    return k if k > 0 else SATU


def resolve(product, kode, qty, harga_satuan=None, variant=None):
    """Terjemahkan input bersatuan alternatif ke basis dasar.

    Mengembalikan dict:
      qty_dasar     : qty dikali konverter
      harga_dasar   : harga per satuan dasar (None bila harga tidak diberikan)
      uom_kode      : kode satuan yang dipakai ('' bila satuan dasar)
      uom_konverter : faktor konversi
      uom_qty       : qty asli sesuai satuan yang dipilih

    Bila UOM nonaktif / kode kosong / satuan tak dikenal, nilai dikembalikan
    apa adanya dengan konverter 1 — jadi pemanggil tidak perlu bercabang.
    """
    qty = _dec(qty, Decimal('0'))
    unit = find_unit(product, kode, variant) if (kode and is_uom_enabled()) else None

    if unit is None:
        return {
            'qty_dasar': qty,
            'harga_dasar': _dec(harga_satuan, None) if harga_satuan is not None else None,
            'uom_kode': '',
            'uom_konverter': SATU,
            'uom_qty': None,
        }

    k = konverter(unit)
    harga_dasar = None
    if harga_satuan is not None:
        harga_dasar = _dec(harga_satuan, Decimal('0')) / k

    return {
        'qty_dasar': qty * k,
        'harga_dasar': harga_dasar,
        'uom_kode': str(unit.get('kode_satuan', '') or ''),
        'uom_konverter': k,
        'uom_qty': qty,
    }
