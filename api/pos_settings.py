"""Pembacaan pengaturan POS untuk **penegakan aturan** di sisi server.

Sebelumnya seluruh setelan di menu Pengaturan POS hanya tersimpan di
`SystemConfig` tanpa ada satu pun kode yang membacanya saat transaksi berjalan —
sehingga toggle bisa diaktifkan tapi POS tetap berperilaku sama.

Modul ini menjadi satu-satunya sumber pembacaan setelan tersebut. Penegakan
dilakukan di server agar tidak bisa dilewati dari browser; UI hanya cermin agar
kasir dapat umpan balik lebih awal.
"""
import json

from .models import SystemConfig

# Default harus sama dengan default di serializer business-settings.
DEFAULT_BLOKIR_JUAL_KOSONG = True
DEFAULT_EXT = {
    'block_sell_less_than_buy_price': False,
    'disable_add_custom_item': False,
    'hide_remaining_stock': False,
    'staff_only_see_same_day_tx': False,
}


def _raw(key):
    cfg = SystemConfig.objects.filter(key=key).first()
    return cfg.value if cfg else None


def get_bool(key, default=False):
    """Setelan boolean bertipe `SystemConfig` ('True'/'False' sebagai teks)."""
    val = _raw(key)
    if val is None:
        return default
    return str(val).strip().lower() == 'true'


def get_ext_settings():
    """Isi `pos_ext_settings` (JSON) digabung dengan default."""
    hasil = DEFAULT_EXT.copy()
    val = _raw('pos_ext_settings')
    if not val:
        return hasil
    try:
        loaded = json.loads(val)
        if isinstance(loaded, dict):
            hasil.update(loaded)
    except (ValueError, TypeError):
        pass
    return hasil


def ext(nama, default=False):
    return bool(get_ext_settings().get(nama, default))


# --- Pintasan yang dipakai alur transaksi ---
def blokir_jual_jika_stok_kosong():
    """Menu Cek Stok: tolak penjualan bila stok tidak mencukupi.

    Pada mode stok POS 'off' pengecekan ini dimatikan — tidak masuk akal
    memblokir karena stok kosong bila POS memang tidak melacak stok.
    """
    if mode_stok_pos() == 'off':
        return False
    return get_bool('pos_stok_blokir_jual_jika_kosong', DEFAULT_BLOKIR_JUAL_KOSONG)


def blokir_hapus_produk_jika_ada_stok():
    """Menu Cek Stok: cegah hapus produk yang stoknya masih ada."""
    return get_bool('pos_stok_blokir_hapus_jika_ada', False)


def blokir_harga_dibawah_harga_beli():
    """Ext Settings: POS tidak boleh menjual di bawah harga beli."""
    return ext('block_sell_less_than_buy_price')


def blokir_tahan_pesanan():
    """Ext Settings: pesanan harus diselesaikan, tidak boleh ditahan/antri."""
    return ext('disable_hold_queue')


def blokir_tambah_tipe_kas():
    """Ext Settings: kasir tidak boleh menambah tipe Kas Masuk/Keluar."""
    return ext('disable_add_cash_io_type')


def staf_hanya_transaksi_hari_ini():
    """Ext Settings: staff POS hanya melihat transaksi hari yang sama."""
    return ext('staff_only_see_same_day_tx')


def sembunyikan_transaksi_perangkat_lain():
    """Ext Settings: kasir hanya melihat transaksi yang ia buat sendiri."""
    return ext('hide_other_device_online_tx')


def nomor_order_reset_harian():
    """Ext Settings: penomoran POS berurutan dan direset tiap hari."""
    return ext('order_no_reset_daily')


def mode_stok_pos():
    """Mode Stok POS (Produk & Inventori > Mode Stok POS):

      auto   — stok berkurang otomatis tiap transaksi POS (default)
      manual — POS tidak mengubah stok; stok hanya lewat dokumen stok
      off    — POS tidak melacak stok sama sekali (cek stok juga dilewati)
    """
    nilai = (_raw('pos_stock_mode') or 'auto').strip().lower()
    return nilai if nilai in ('auto', 'manual', 'off') else 'auto'


def pos_mengurangi_stok():
    """Hanya mode 'auto' yang memotong stok saat penjualan POS."""
    return mode_stok_pos() == 'auto'


def wajib_shift_aktif():
    """Menu Shift: transaksi POS hanya boleh saat ada shift terbuka."""
    return get_bool('pos_shift_aktif', False)


# --- PassKey: PIN otorisasi untuk tindakan sensitif di POS ---
PASSKEY_AKSI = {
    'diskon': ('pos_passkey_diskon_aktif', 'pos_passkey_diskon_val'),
    'pelanggan': ('pos_passkey_pelanggan_aktif', 'pos_passkey_pelanggan_val'),
    'belum_bayar': ('pos_passkey_belum_bayar_aktif', 'pos_passkey_belum_bayar_val'),
    'sudah_bayar': ('pos_passkey_sudah_bayar_aktif', 'pos_passkey_sudah_bayar_val'),
}


def passkey_aktif(aksi):
    pasangan = PASSKEY_AKSI.get(aksi)
    return get_bool(pasangan[0], False) if pasangan else False


def passkey_cocok(aksi, pin):
    """True bila PIN benar. Bila PassKey untuk aksi ini tidak aktif, selalu True."""
    pasangan = PASSKEY_AKSI.get(aksi)
    if not pasangan or not get_bool(pasangan[0], False):
        return True
    tersimpan = (_raw(pasangan[1]) or '').strip()
    if not tersimpan:
        # Aktif tapi PIN belum diatur -> jangan mengunci kasir.
        return True
    return str(pin or '').strip() == tersimpan
