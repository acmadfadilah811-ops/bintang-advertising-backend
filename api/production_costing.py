"""Alokasi biaya produksi ke HPP barang jadi.

Dipisah dari product_views.py yang sudah 2.100+ baris (batas AGENTS.md: 400).

Penyerapan bersifat opt-in per dokumen lewat
StockProductionDocument.serap_biaya_ke_hpp. Bila mati, dokumen berperilaku
persis seperti sebelum fitur ini ada: lapisan FIFO dibuat senilai harga_beli
produk saja.
"""

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal('0')
# StockLayer.harga_beli hanya menyimpan 2 desimal, jadi biaya per unit tidak
# bisa lebih halus dari ini. Untuk biaya kecil yang dibagi ke sangat banyak
# unit, sebagian nilai bisa hilang karena pembulatan.
SEN = Decimal('0.01')


def _dec(value):
    """Ubah apa pun jadi Decimal dengan aman (hindari campur float/Decimal)."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def biaya_per_unit(document, items):
    """Biaya produksi per unit untuk tiap item, proporsional terhadap nilai bahan.

    Bobot tiap item = harga_beli x qty, sehingga item yang bahannya lebih mahal
    menyerap biaya lebih besar. Mengembalikan {item_id: Decimal}, selalu berisi
    semua item (0 bila tidak menyerap) supaya pemanggil tidak perlu cek None.
    """
    kosong = {item.id: ZERO for item in items}

    if not document.serap_biaya_ke_hpp or not items:
        return kosong

    total_biaya = sum((_dec(b.nilai) for b in document.biaya.all()), ZERO)
    if total_biaya <= 0:
        return kosong

    bobot = {item.id: _dec(item.product.harga_beli) * _dec(item.qty) for item in items}
    total_bobot = sum(bobot.values(), ZERO)

    if total_bobot <= 0:
        # Semua bahan bernilai 0 -> tidak ada dasar proporsi. Jatuh ke rata per
        # unit supaya biaya tetap terserap, bukan hilang diam-diam.
        total_qty = sum((_dec(item.qty) for item in items), ZERO)
        if total_qty <= 0:
            return kosong
        rata = (total_biaya / total_qty).quantize(SEN, rounding=ROUND_HALF_UP)
        return {item.id: rata for item in items}

    hasil = {}
    terpakai = ZERO
    terakhir = len(items) - 1
    for index, item in enumerate(items):
        if index == terakhir:
            # Sisa pembulatan dilimpahkan ke item terakhir supaya jumlah porsi
            # persis sama dengan total biaya dokumen.
            porsi = total_biaya - terpakai
        else:
            porsi = (total_biaya * bobot[item.id] / total_bobot).quantize(
                SEN, rounding=ROUND_HALF_UP
            )
            terpakai += porsi

        qty = _dec(item.qty)
        hasil[item.id] = (
            (porsi / qty).quantize(SEN, rounding=ROUND_HALF_UP) if qty > 0 else ZERO
        )
    return hasil
