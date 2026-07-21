"""Mesin evaluasi aturan marketing: kupon, promosi POS, dan diskon penjualan.

Modul ini murni perhitungan — tidak menyentuh database untuk menulis. Pemanggil
(`pos_services.create_sale`, alur Order) yang bertanggung jawab menyimpan hasil
dan mencatat `CouponUsage` di dalam transaksinya sendiri. Pemisahan ini membuat
mesin bisa dipakai dua kali: sekali untuk pratinjau (endpoint validasi, tanpa
efek samping) dan sekali saat transaksi benar-benar disimpan.

Semua uang memakai Decimal. Menghitung diskon dengan float akan menghasilkan
selisih receh yang tidak pernah cocok saat rekonsiliasi kas.

BATASAN YANG DIKETAHUI
`paket_produk` belum bisa dicocokkan di tingkat baris: POSSaleItem dan OrderItem
menyimpan FK ke Product, bukan ke ProductPackage, jadi tidak ada cara mengenali
"baris ini berasal dari paket X". Kriteria itu diterima dan disimpan, tapi
`_baris_cocok()` mengabaikannya — lihat `PAKET_BELUM_DIDUKUNG`. Menjadikannya
diam-diam cocok akan memberi diskon ke transaksi yang tidak berhak.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.utils import timezone

from .marketing_models import KANAL_ONLINE, KANAL_POS

MONEY = Decimal('0.01')
NOL = Decimal('0')

PAKET_BELUM_DIDUKUNG = True

# Python weekday(): Senin=0 ... Minggu=6. Kode hari mengikuti nilai default
# kolom POSPromotion.hari ('min,sen,sel,rab,kam,jum,sab').
KODE_HARI = ['sen', 'sel', 'rab', 'kam', 'jum', 'sab', 'min']


def money(value):
    """Bulatkan ke 2 desimal dengan ROUND_HALF_UP; nilai tak valid jadi nol."""
    try:
        return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return NOL


# ---------------------------------------------------------------------------
# Struktur data
# ---------------------------------------------------------------------------

@dataclass
class BarisKeranjang:
    """Satu baris keranjang yang sudah dinormalkan oleh pemanggil.

    `subtotal` selalu dihitung server (harga dari DB × qty), tidak pernah
    diambil dari klien — lihat pos_services.create_sale.
    """
    product: object = None
    variant: object = None
    qty: Decimal = NOL
    harga: Decimal = NOL
    subtotal: Decimal = NOL
    ref: object = None  # penanda bebas milik pemanggil (mis. index item)


@dataclass
class KonteksPromo:
    baris: list
    subtotal: Decimal
    pelanggan: object = None
    kanal: str = KANAL_POS
    saat: object = None

    def __post_init__(self):
        if self.saat is None:
            self.saat = timezone.localtime()


@dataclass
class ItemGratis:
    product: object
    qty: Decimal
    promo: object = None


@dataclass
class HasilKupon:
    ok: bool = False
    alasan: str = ''
    kupon: object = None
    diskon: Decimal = NOL
    basis: Decimal = NOL


@dataclass
class HasilPromo:
    diskon: Decimal = NOL
    items_gratis: list = field(default_factory=list)
    diterapkan: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Matcher bersama
# ---------------------------------------------------------------------------

def _dalam_jendela_tanggal(rule, saat):
    tanggal = saat.date()
    aktif = getattr(rule, 'tanggal_aktif', None)
    if aktif and tanggal < aktif:
        return False, f'Belum aktif — berlaku mulai {aktif:%d-%m-%Y}.'
    if not getattr(rule, 'tanpa_kadaluarsa', True):
        akhir = getattr(rule, 'tanggal_kadaluarsa', None)
        if akhir and tanggal > akhir:
            return False, f'Sudah kedaluwarsa sejak {akhir:%d-%m-%Y}.'
    return True, ''


def _dalam_jendela_jam(rule, saat):
    """Hanya POSPromotion punya batasan jam/hari; kupon tidak."""
    if getattr(rule, 'jam_24', True):
        jam_ok = True
    else:
        mulai = getattr(rule, 'jam_mulai', None)
        akhir = getattr(rule, 'jam_berakhir', None)
        jam = saat.time()
        if mulai and akhir and mulai <= akhir:
            jam_ok = mulai <= jam <= akhir
        elif mulai and akhir:
            # Jendela melewati tengah malam, mis. 22:00–02:00.
            jam_ok = jam >= mulai or jam <= akhir
        else:
            jam_ok = True
    if not jam_ok:
        return False, 'Di luar jam berlaku promosi.'

    hari_raw = getattr(rule, 'hari', '') or ''
    if hari_raw:
        aktif = {h.strip().lower() for h in hari_raw.split(',') if h.strip()}
        if aktif and KODE_HARI[saat.weekday()] not in aktif:
            return False, 'Tidak berlaku di hari ini.'
    return True, ''


def _pelanggan_cocok(rule, pelanggan):
    if getattr(rule, 'all_customers', True):
        return True, ''
    if pelanggan is None:
        return False, 'Kupon ini khusus pelanggan terdaftar — pilih pelanggan dulu.'
    if rule.pelanggan.filter(pk=pelanggan.pk).exists():
        return True, ''
    return False, 'Pelanggan ini tidak termasuk sasaran kupon.'


def _baris_cocok(rule, baris, id_produk, id_kategori, id_brand):
    """Apakah satu baris keranjang masuk cakupan produk sebuah aturan.

    Kumpulan id di-precompute pemanggil supaya tidak query per-baris.
    """
    semua_produk = getattr(rule, 'all_products', True)
    semua_brand = getattr(rule, 'all_brands', True)
    # all_packages sengaja tidak ikut menentukan: lihat PAKET_BELUM_DIDUKUNG.
    if semua_produk and semua_brand:
        return True

    produk = baris.product
    if produk is None:
        # Item non-katalog tidak bisa dibuktikan masuk cakupan.
        return False

    if not semua_produk:
        if id_produk and produk.pk in id_produk:
            return True
        if id_kategori and produk.kategori_id and produk.kategori_id in id_kategori:
            return True
    if not semua_brand:
        if id_brand and produk.brand_id and produk.brand_id in id_brand:
            return True
    return False


def _id_cakupan(rule):
    """Ambil sekali semua id relasi cakupan produk sebuah aturan."""
    return (
        set(rule.produk.values_list('pk', flat=True)) if hasattr(rule, 'produk') else set(),
        set(rule.grup_produk.values_list('pk', flat=True)) if hasattr(rule, 'grup_produk') else set(),
        set(rule.brand.values_list('pk', flat=True)) if hasattr(rule, 'brand') else set(),
    )


def _potongan(tipe, jumlah, basis, maksimal=None):
    """Hitung potongan dan kunci agar tidak pernah melebihi basisnya."""
    basis = money(basis)
    if basis <= NOL:
        return NOL
    if tipe == 'percent':
        nilai = money(basis * money(jumlah) / Decimal('100'))
    else:
        nilai = money(jumlah)
    if maksimal is not None and money(maksimal) > NOL:
        nilai = min(nilai, money(maksimal))
    return min(nilai, basis)


# ---------------------------------------------------------------------------
# Kupon Diskon
# ---------------------------------------------------------------------------

def _batas_penggunaan_terlampaui(kupon, pelanggan, tanggal):
    """Cek batas pakai dari riwayat CouponUsage, bukan dari counter.

    Counter tunggal tidak bisa menjawab "pelanggan ini sudah pernah pakai?"
    maupun "sudah dipakai hari ini?", dan tidak ikut turun saat nota di-void.
    """
    from .marketing_models import CouponUsage

    riwayat = CouponUsage.objects.filter(kupon=kupon)

    if not kupon.unlimited_usage and kupon.batas_penggunaan is not None:
        if riwayat.count() >= kupon.batas_penggunaan:
            return 'Kuota kupon sudah habis.'

    if kupon.once_per_customer:
        if pelanggan is None:
            return 'Kupon ini dibatasi satu kali per pelanggan — pilih pelanggan dulu.'
        if riwayat.filter(pelanggan=pelanggan).exists():
            return 'Pelanggan ini sudah pernah memakai kupon tersebut.'

    if not kupon.daily_reuse and pelanggan is not None:
        if riwayat.filter(pelanggan=pelanggan, tanggal=tanggal).exists():
            return 'Kupon ini hanya bisa dipakai sekali per hari untuk pelanggan yang sama.'

    return ''


def evaluate_coupon(kupon, konteks):
    """Nilai satu kupon terhadap keranjang. Tidak menulis apa pun.

    Urutan pemeriksaan dibuat dari yang paling murah dan paling informatif,
    supaya pesan penolakan yang sampai ke kasir adalah alasan sebenarnya.
    """
    hasil = HasilKupon(kupon=kupon)

    if not kupon.is_active:
        hasil.alasan = 'Kupon tidak aktif.'
        return hasil

    if konteks.kanal == KANAL_POS and not kupon.show_pos:
        hasil.alasan = 'Kupon ini tidak berlaku di kasir.'
        return hasil
    if konteks.kanal == KANAL_ONLINE and not kupon.show_online:
        hasil.alasan = 'Kupon ini tidak berlaku untuk pesanan online.'
        return hasil

    ok, alasan = _dalam_jendela_tanggal(kupon, konteks.saat)
    if not ok:
        hasil.alasan = alasan
        return hasil

    ok, alasan = _pelanggan_cocok(kupon, konteks.pelanggan)
    if not ok:
        hasil.alasan = alasan
        return hasil

    if money(konteks.subtotal) < money(kupon.min_total_pesanan):
        hasil.alasan = f'Minimal belanja Rp {money(kupon.min_total_pesanan):,.0f} belum terpenuhi.'
        return hasil

    alasan = _batas_penggunaan_terlampaui(kupon, konteks.pelanggan, konteks.saat.date())
    if alasan:
        hasil.alasan = alasan
        return hasil

    id_produk, id_kategori, id_brand = _id_cakupan(kupon)
    basis = sum(
        (money(b.subtotal) for b in konteks.baris
         if _baris_cocok(kupon, b, id_produk, id_kategori, id_brand)),
        NOL,
    )
    if basis <= NOL:
        hasil.alasan = 'Tidak ada barang di keranjang yang termasuk cakupan kupon.'
        return hasil

    hasil.basis = money(basis)
    hasil.diskon = _potongan(kupon.tipe_diskon, kupon.jumlah_diskon, basis,
                             kupon.maksimal_jumlah_diskon)
    if hasil.diskon <= NOL:
        hasil.alasan = 'Potongan kupon bernilai nol.'
        return hasil

    hasil.ok = True
    return hasil


def evaluate_coupon_code(kode, konteks):
    """Cari kupon berdasarkan kode lalu nilai. Kode dicocokkan case-insensitive."""
    from .marketing_models import DiscountCoupon

    kode = (kode or '').strip()
    if not kode:
        return HasilKupon(alasan='Kode kupon kosong.')
    kupon = DiscountCoupon.objects.filter(kode__iexact=kode).first()
    if kupon is None:
        return HasilKupon(alasan='Kode kupon tidak ditemukan.')
    return evaluate_coupon(kupon, konteks)


# ---------------------------------------------------------------------------
# Promosi POS (BX / DQ / DA / FI)
# ---------------------------------------------------------------------------

def _syarat_produk(promo):
    """produk_qty -> {product_id: qty_minimal}. Entri tanpa product_id dilewati.

    Entri lama hanya menyimpan nama; migrasi 0078 mengisi `product_id`. Yang
    tetap kosong berarti namanya tidak ketemu — dilewati, bukan dicocokkan
    ulang berdasarkan nama, supaya promo tidak menyala karena kemiripan teks.
    """
    syarat = {}
    for entry in (promo.produk_qty or []):
        if not isinstance(entry, dict):
            continue
        pid = entry.get('product_id')
        if not pid:
            continue
        try:
            qty = Decimal(str(entry.get('qty', 1) or 1))
        except (InvalidOperation, TypeError, ValueError):
            qty = Decimal('1')
        if qty <= NOL:
            continue
        syarat[int(pid)] = syarat.get(int(pid), NOL) + qty
    return syarat


def _qty_per_produk(konteks):
    total = {}
    for b in konteks.baris:
        if b.product is None:
            continue
        total[b.product.pk] = total.get(b.product.pk, NOL) + Decimal(str(b.qty or 0))
    return total


def _hitung_pemicu(promo, konteks):
    """Berapa kali syarat promosi terpenuhi oleh keranjang.

    0 berarti tidak memenuhi. Bila `berlaku_kelipatan` mati, hasilnya dipatok 1
    supaya hadiah/diskon hanya diberikan sekali betapa pun besar keranjangnya.
    """
    syarat = _syarat_produk(promo)
    qty_cart = _qty_per_produk(konteks)

    if syarat:
        if promo.combine_qty:
            ambang = Decimal(str(promo.combine_qty_value or 1))
            terkumpul = sum((qty_cart.get(pid, NOL) for pid in syarat), NOL)
            pemicu = int(terkumpul // ambang) if ambang > NOL else 0
        else:
            rasio = [int(qty_cart.get(pid, NOL) // butuh) if butuh > NOL else 0
                     for pid, butuh in syarat.items()]
            if promo.berlaku_membeli == 'salah-satu':
                pemicu = max(rasio) if rasio else 0
            else:
                pemicu = min(rasio) if rasio else 0
    elif money(promo.min_total_transaksi) > NOL:
        ambang = money(promo.min_total_transaksi)
        pemicu = int(money(konteks.subtotal) // ambang) if ambang > NOL else 0
    else:
        # Tanpa syarat produk maupun ambang nominal, promosi hanya berlaku bila
        # keranjang tidak kosong — jangan sampai menyala di transaksi kosong.
        pemicu = 1 if konteks.baris else 0

    if pemicu <= 0:
        return 0
    return pemicu if promo.berlaku_kelipatan else 1


def evaluate_promotions(konteks, promosi=None):
    """Nilai semua promosi POS yang aktif terhadap keranjang.

    Mengembalikan total potongan, daftar item gratis, dan rincian promosi yang
    menyala. Tidak menulis apa pun — pemanggil yang membuat baris hadiah dan
    memotong stoknya.
    """
    from .marketing_models import POSPromotion

    hasil = HasilPromo()
    if promosi is None:
        promosi = (POSPromotion.objects.filter(is_active=True)
                   .prefetch_related('produk_gratis', 'grup_produk', 'brand', 'pelanggan'))

    for promo in promosi:
        ok, _ = _dalam_jendela_tanggal(promo, konteks.saat)
        if not ok:
            continue
        ok, _ = _dalam_jendela_jam(promo, konteks.saat)
        if not ok:
            continue
        ok, _ = _pelanggan_cocok(promo, konteks.pelanggan)
        if not ok:
            continue

        pemicu = _hitung_pemicu(promo, konteks)
        if pemicu <= 0:
            continue

        diskon = NOL
        gratis = []

        if promo.tipe_promosi in ('BX', 'FI'):
            qty_gratis = Decimal(str(promo.qty_gratis or 1))
            for produk in promo.produk_gratis.all():
                gratis.append(ItemGratis(product=produk,
                                         qty=qty_gratis * Decimal(pemicu),
                                         promo=promo))

        elif promo.tipe_promosi == 'DQ':
            id_produk, id_kategori, id_brand = _id_cakupan(promo)
            syarat = _syarat_produk(promo)
            basis = NOL
            for b in konteks.baris:
                cocok = (b.product is not None and b.product.pk in syarat) if syarat else \
                    _baris_cocok(promo, b, id_produk, id_kategori, id_brand)
                if cocok:
                    basis += money(b.subtotal)
            if promo.tipe_diskon == 'nominal':
                diskon = _potongan('nominal', money(promo.jumlah_diskon) * Decimal(pemicu), basis)
            else:
                diskon = _potongan('percent', promo.jumlah_diskon, basis)

        elif promo.tipe_promosi == 'DA':
            if money(konteks.subtotal) < money(promo.min_total_transaksi):
                continue
            if promo.tipe_diskon == 'nominal':
                diskon = _potongan('nominal', money(promo.jumlah_diskon) * Decimal(pemicu),
                                   konteks.subtotal)
            else:
                diskon = _potongan('percent', promo.jumlah_diskon, konteks.subtotal)

        if diskon <= NOL and not gratis:
            continue

        hasil.diskon += diskon
        hasil.items_gratis.extend(gratis)
        hasil.diterapkan.append({
            'promo': promo,
            'judul': promo.judul,
            'tipe': promo.tipe_promosi,
            'pemicu': pemicu,
            'diskon': diskon,
        })

    hasil.diskon = money(hasil.diskon)
    return hasil


# ---------------------------------------------------------------------------
# Diskon Penjualan (otomatis, kanal online)
# ---------------------------------------------------------------------------

def evaluate_sales_discount(konteks, diskon=None):
    """Diskon Penjualan berlaku otomatis di Toko Online saat minimal terpenuhi.

    Bila beberapa aturan terpenuhi sekaligus, yang dipakai adalah yang paling
    menguntungkan pelanggan — bukan yang pertama ditemukan, supaya hasilnya
    tidak bergantung urutan baris di tabel.
    """
    from .marketing_models import SalesDiscount

    if konteks.kanal != KANAL_ONLINE:
        return NOL, None

    if diskon is None:
        diskon = SalesDiscount.objects.filter(is_active=True).prefetch_related('brand')

    terbaik = NOL
    aturan_terbaik = None
    for aturan in diskon:
        ok, _ = _dalam_jendela_tanggal(aturan, konteks.saat)
        if not ok:
            continue
        if money(konteks.subtotal) < money(aturan.minimal_total_pesanan):
            continue

        id_brand = set(aturan.brand.values_list('pk', flat=True))
        if id_brand:
            basis = sum((money(b.subtotal) for b in konteks.baris
                         if b.product is not None and b.product.brand_id in id_brand), NOL)
        else:
            basis = money(konteks.subtotal)
        if basis <= NOL:
            continue

        nilai = _potongan(aturan.tipe_diskon, aturan.jumlah_diskon, basis)
        if nilai > terbaik:
            terbaik = nilai
            aturan_terbaik = aturan

    return money(terbaik), aturan_terbaik
