"""
wa_logic.py — Logika Bot WhatsApp Bintang Advertising (Django)
Sistem baru: order masuk → tunggu konfirmasi staff

Alur percakapan:
  Sapaan → Tanya nama (kalau baru)
  Tanya produk/katalog → Info produk (TANPA langsung kirim form)
  Tanya harga → Jawab harga detail (TANPA form)
  Eksplisit mau order → Kirim form (1 form bisa banyak item)
  Kirim form + DATA SUDAH SESUAI → Simpan & konfirmasi
  Cek status → Tracking multi-item
  Lainnya → AI Fallback
"""

import os
import re
import difflib


def get_business_name():
    from .models import SystemConfig
    try:
        return SystemConfig.objects.get(key='bisnis_nama').value or 'Brandy'
    except Exception:
        return 'Brandy'


# ── AI Client (KoboiLLM — OpenAI-compatible) ──────────────────────────
def get_ai_client():
    from openai import OpenAI
    return OpenAI(
        api_key=os.getenv("KOBOI_API_KEY"),
        base_url="https://api.koboillm.com/v1",
        timeout=15.0  # Prevent hanging connection indefinitely
    )

import logging
logger = logging.getLogger(__name__)

# ── State in cache (production-ready & shared across processes) ───────
class CacheSet:
    def __init__(self, cache_prefix="wa_menunggu_nama_"):
        self.prefix = cache_prefix
        
    def __contains__(self, item):
        from django.core.cache import cache
        return cache.get(f"{self.prefix}{item}", False)
        
    def add(self, item):
        from django.core.cache import cache
        cache.set(f"{self.prefix}{item}", True, timeout=3600) # 1 jam timeout
        
    def discard(self, item):
        from django.core.cache import cache
        cache.delete(f"{self.prefix}{item}")

menunggu_nama = CacheSet()


def ekstrak_nama_dari_pesan(pesan):
    """
    Ekstrak nama bersih dari kalimat jawaban pelanggan.
    Contoh:
      "Halo nama saya Budi Santoso" → "Budi Santoso"
      "saya fadil" → "Fadil"
      "panggil saja ani" → "Ani"
      "asisten bintang" → "Asisten Bintang" (tetap diambil kalau tidak ada kata sapa)
    Batas maksimal 30 karakter.
    """
    import re
    p = pesan.strip()

    # Buang kata-kata sapaan & pengantar
    prefiks = [
        r'^halo[,\s]+', r'^hai[,\s]+', r'^hi[,\s]+', r'^hey[,\s]+',
        r'^nama\s+saya\s+', r'^nama\s+aku\s+', r'^nama\s+ku\s+',
        r'^saya\s+', r'^aku\s+', r'^gue\s+', r'^gw\s+',
        r'^panggil\s+saja\s+', r'^panggil\s+aja\s+',
        r'^biasa\s+dipanggil\s+', r'^dipanggil\s+',
        r'^ini\s+', r'^dengan\s+',
        # Buang sapaan di awal lalu nama
        r'^(?:halo|hai|hi|hey)[,\s]+(?:nama\s+(?:saya|aku)\s+)?',
        r'^(?:nama\s+)?(?:saya|aku)\s+(?:adalah\s+|ialah\s+)?',
    ]

    hasil = p
    for pola in prefiks:
        hasil = re.sub(pola, '', hasil, flags=re.IGNORECASE).strip()

    # Ambil hanya bagian pertama (sebelum tanda baca atau keterangan tambahan)
    hasil = re.split(r'[,\.!\?\(\)]', hasil)[0].strip()

    # Judul/gelar di akhir (Pak, Bu, dll.) - biarkan saja
    # Batasi panjang nama
    if len(hasil) > 30:
        # Ambil max 3 kata pertama
        kata = hasil.split()
        hasil = ' '.join(kata[:3])

    # Capitalize tiap kata
    hasil = hasil.title() if hasil else pesan.strip()[:30].title()

    return hasil if len(hasil) >= 2 else pesan.strip()[:30].title()


# ════════════════════════════════════════════════════════════════
# SYSTEM PROMPT & MEMORI
# ════════════════════════════════════════════════════════════════

def get_system_prompt(nama_pelanggan=""):
    from .models import ProductPrice, SystemConfig
    import json

    prices = ProductPrice.objects.all()
    data_harga = {}
    for p in prices:
        if p.kategori not in data_harga:
            data_harga[p.kategori] = {}
        data_harga[p.kategori][p.nama_produk] = p.harga
    string_harga = json.dumps(data_harga, ensure_ascii=False, indent=2)

    try:
        conf = SystemConfig.objects.get(pk="system_prompt")
        template_ai = conf.value
    except SystemConfig.DoesNotExist:
        biz_name = get_business_name()
        template_ai = (
            f"Kamu adalah asisten virtual {biz_name} yang ramah dan profesional. "
            "Jawab dalam bahasa Indonesia yang santai dan singkat.\n\n"
            "=== ATURAN WAJIB ===\n"
            "1. Jika pelanggan menyebut ingin mencetak, memesan, membuat, atau order produk apapun, "
            "LANGSUNG kirimkan form order di bawah ini PERSIS APA ADANYA. "
            "JANGAN tanya detail dulu. JANGAN buat form sendiri dengan format berbeda.\n"
            "2. Jika hanya tanya harga atau info produk, jawab singkat tanpa form.\n"
            "3. Jangan mengarang harga — gunakan data harga yang tersedia.\n\n"
            "=== TEMPLATE FORM ORDER (gunakan PERSIS ini, jangan ubah format) ===\n"
            f"📋 *FORM ORDER - {biz_name}*\n"
            "_(Bisa isi lebih dari 1 item)_\n\n"
            "👤 *Data Pemesan*\n"
            "- Nama    : \n"
            "- No. WA  : \n\n"
            "📦 *Item 1*\n"
            "- Jenis Produk  : \n"
            "- Jumlah        : \n"
            "- Ukuran        : \n"
            "- Bahan/Material: \n"
            "- Finishing     : \n"
            "- File Desain   : sudah ada / belum ada\n"
            "- Keterangan    : \n\n"
            "📦 *Item 2 (hapus jika tidak perlu)*\n"
            "- Jenis Produk  : \n"
            "- Jumlah        : \n"
            "- Ukuran        : \n"
            "- Bahan/Material: \n"
            "- Finishing     : \n"
            "- File Desain   : sudah ada / belum ada\n"
            "- Keterangan    : \n\n"
            "⚠️ *PENTING:* Kirimkan form yang sudah diisi lengkap, dan sistem kami akan langsung mendaftarkan pesanan Kakak secara otomatis. 👇\n"
            "=== AKHIR TEMPLATE ===\n\n"
            "Saat mengirim form, awali dengan: 'Siap Kak! Silakan *copy* dan isi form berikut:'"
        )

    return f"{template_ai}\n\nData harga produk:\n{string_harga}\n"


def get_memori_percakapan(nomor, nama_pelanggan=""):
    from django.core.cache import cache
    cache_key = f"wa_memori_{nomor}"
    history = cache.get(cache_key)
    if not history:
        history = [{"role": "system", "content": get_system_prompt(nama_pelanggan)}]
    else:
        history[0]['content'] = get_system_prompt(nama_pelanggan)
    return history


def simpan_ke_memori(nomor, role, konten, nama_pelanggan=""):
    from django.core.cache import cache
    cache_key = f"wa_memori_{nomor}"
    history = get_memori_percakapan(nomor, nama_pelanggan)
    history.append({"role": role, "content": konten})
    if len(history) > 11:
        history = [history[0]] + history[-10:]
    cache.set(cache_key, history, timeout=86400) # Simpan 24 jam


# ════════════════════════════════════════════════════════════════
# TRACKING PESANAN
# ════════════════════════════════════════════════════════════════

STATUS_LABEL = {
    'antrean':    ('⏳', 'Dalam antrean, segera diproses tim kami'),
    'dikerjakan': ('🔧', 'Sedang dikerjakan oleh tim produksi'),
    'selesai':    ('✅', 'Selesai diproduksi'),
    'gagal':      ('❌', 'Terdapat kendala — mohon hubungi admin'),
}


def format_tracking(order, panggilan="Kak"):
    status_map = {
        'draft': 'Draft Penawaran',
        'quotation': 'Kirim Penawaran',
        'review': 'Menunggu Review Manager',
        'desain': 'Proses Desain',
        'proses': 'Dalam Proses Produksi',
        'ready': 'Siap Diambil / Selesai Produksi',
        'selesai': 'Selesai Seluruhnya',
        'batal': 'Dibatalkan / Cancel',
    }
    status_display = status_map.get(order.status_global, order.status_global.upper())

    lines = [
        f"📦 *STATUS PESANAN ({order.id})*",
        f"👤 *Pemesan*: {order.nama or '-'}",
        f"📋 *Status*: {status_display}",
        "",
    ]

    items = order.items.prefetch_related('jobs').all()
    if not items.exists():
        lines.append("_Belum ada item dalam pesanan ini._")
    else:
        lines.append(f"🛒 *{items.count()} Item Pesanan:*")
        for i, item in enumerate(items, 1):
            lines.append(f"\n  *{i}. {item.jenis_produk}* (qty: {item.qty})")
            latest_job = item.jobs.order_by('-id').first()
            if latest_job:
                emoji, deskripsi = STATUS_LABEL.get(
                    latest_job.status_pekerjaan,
                    ('🔄', latest_job.status_pekerjaan)
                )
                lines.append(f"     {emoji} {deskripsi}")
                if latest_job.tahap:
                    lines.append(f"     📍 Tahap: {latest_job.tahap.nama}")
            else:
                lines.append("     ⏳ Menunggu diproses")

            if item.harga_jual and item.harga_jual > 0:
                lines.append(f"     💰 Harga: Rp {item.harga_jual:,}".replace(',', '.'))

    # Tentukan footer dinamis berdasarkan status_global dan status job riil
    status = order.status_global
    has_desain_job = False
    has_proses_job = False

    for item in items:
        for job in item.jobs.all():
            if job.tahap:
                tahap_lower = job.tahap.nama.lower()
                divisi_lower = job.tahap.divisi.nama.lower() if job.tahap.divisi else ''
                if 'desain' in tahap_lower or 'design' in tahap_lower or 'desain' in divisi_lower or 'design' in divisi_lower:
                    has_desain_job = True
                if 'cetak' in tahap_lower or 'print' in tahap_lower or 'proses' in tahap_lower or 'produksi' in tahap_lower or 'cetak' in divisi_lower or 'print' in divisi_lower or 'produksi' in divisi_lower:
                    has_proses_job = True

    if status == 'batal':
        footer = f"\n_Pesanan ini telah dibatalkan. Silakan hubungi kami jika ada pertanyaan. 🙏_"
    elif status == 'selesai':
        footer = f"\n_Pesanan {panggilan} sudah selesai diserahterimakan. Terima kasih banyak atas kepercayaan Kakak pada Bintang Advertising! 😊_"
    elif status == 'ready':
        footer = f"\n_Pesanan {panggilan} sudah selesai diproduksi dan siap diambil/dikirim! Silakan hubungi admin untuk pengambilan ya Kak! 🎉_"
    elif status == 'proses' or has_proses_job:
        footer = f"\n_Pesanan {panggilan} sedang diproduksi di workshop kami. Kami akan mengabari Kakak begitu pesanan siap! 🔧_"
    elif status == 'desain' or has_desain_job:
        footer = f"\n_Pesanan {panggilan} saat ini sedang dalam tahap pembuatan desain oleh desainer kami. Mohon ditunggu ya! 🎨_"
    elif status in ('draft', 'review', 'quotation'):
        footer = f"\n_Pesanan sudah kami catat {panggilan}. Tim kami sedang memverifikasi rincian pesanan Kakak. Mohon ditunggu ya! 🙏_"
    else:
        footer = f"\n_Pesanan sudah kami catat {panggilan}. Tim kami akan segera menghubungi Kakak. Mohon ditunggu ya! 🙏_"

    lines.append(footer)
    return "\n".join(lines)


def cek_tracking(pesan, nomor, nama_pelanggan):
    from .models import Order
    p = pesan.lower()
    panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

    # Hanya trigger jika ada keyword tracking yang SPESIFIK
    keyword_tracking = ['cek pesanan', 'cek order', 'lacak', 'tracking', 'status pesanan', 'ord-']
    if not any(k in p for k in keyword_tracking):
        return None

    match = re.search(r'(ord-[\w-]+)', p)
    if match:
        id_cari = match.group(1).upper()
        try:
            order = Order.objects.prefetch_related('items__jobs').get(id=id_cari)
            return format_tracking(order, panggilan)
        except Order.DoesNotExist:
            return (
                f"Maaf {panggilan}, pesanan *{id_cari}* tidak ditemukan. "
                f"Pastikan ID pesanan sudah benar ya Kak 🙏"
            )
    else:
        # Cari by nomor WA jika tidak ada ID spesifik
        orders = Order.objects.filter(nomor_wa=nomor).order_by('-waktu')[:3]
        if orders.exists():
            if orders.count() == 1:
                return format_tracking(orders.first(), panggilan)
            lines = [f"📋 {panggilan} punya {orders.count()} pesanan terakhir:\n"]
            for o in orders:
                item_pertama = o.items.first()
                produk = item_pertama.jenis_produk if item_pertama else 'Umum'
                jml_item = o.items.count()
                lines.append(f"• *{o.id}* — {produk}{' +lainnya' if jml_item > 1 else ''} ({o.status_global.upper()})")
            lines.append("\nKetik *Cek [ID]* untuk lihat detail, contoh: _Cek ORD-20260517-XXXX_")
            return "\n".join(lines)
        else:
            return (
                f"Maaf {panggilan}, belum ada pesanan atas nomor ini.\n"
                f"Jika sudah pernah pesan, kirimkan ID pesanannya ya.\n"
                f"Contoh: *Cek ORD-20260517-XXXX*"
            )


def proses_kirim_desain(pesan, nomor, nama_pelanggan):
    from .models import Order, OrderActivityLog
    p = pesan.lower().strip()
    panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

    if 'kirim desain' not in p:
        return None

    # Cari Order ID
    match = re.search(r'(ord-[\w-]+)', p)
    if not match:
        return (
            f"Mohon sertakan ID Pesanan Kakak untuk mengirim desain susulan.\n"
            f"Format: *Kirim Desain [ID Pesanan] [Link Google Drive]*\n"
            f"Contoh: *Kirim Desain ORD-20260606-XXXX https://drive.google.com/...*"
        )

    order_id = match.group(1).upper()
    try:
        order = Order.objects.get(id__iexact=order_id)
    except Order.DoesNotExist:
        return f"Maaf {panggilan}, ID pesanan *{order_id}* tidak ditemukan. Mohon periksa kembali ya Kak 🙏"

    # Validasi nomor WA
    cleaned_input = ''.join(filter(str.isdigit, nomor))
    cleaned_db = ''.join(filter(str.isdigit, order.nomor_wa))
    if cleaned_input[-9:] != cleaned_db[-9:]:
        return f"Maaf {panggilan}, nomor WhatsApp ini tidak cocok dengan data pemesan ID *{order_id}*."

    # Cari URL
    url_match = re.search(r'(https?://[^\s]+)', pesan)
    if not url_match:
        return (
            f"Silakan sertakan link file desain Kakak (misal: link Google Drive atau Dropbox).\n"
            f"Contoh: *Kirim Desain {order_id} https://drive.google.com/...*"
        )

    gdrive_link = url_match.group(1)
    
    # Simpan ke order items
    items = order.items.all()
    if not items.exists():
        return f"Belum ada item produk di pesanan *{order_id}*."

    updated = False
    for item in items:
        if not item.gdrive_customer_link or items.count() == 1:
            item.gdrive_customer_link = gdrive_link
            item.desain_susulan = True
            item.save()
            updated = True

    if not updated:
        first_item = items.first()
        first_item.gdrive_customer_link = gdrive_link
        first_item.desain_susulan = True
        first_item.save()

    # Catat di OrderActivityLog
    OrderActivityLog.objects.create(
        order=order,
        user=None,
        tindakan="SUBMIT_DESIGN_SUSULAN",
        keterangan=f"Pelanggan mengirim link desain susulan via WA: {gdrive_link}"
    )

    return (
        f"Terima kasih {panggilan}! Link desain untuk pesanan *{order_id}* berhasil kami simpan. ✅\n\n"
        f"Tim desain kami akan segera meninjau dan memproses pesanan Kakak. Mohon ditunggu ya! 😊"
    )


# ════════════════════════════════════════════════════════════════
# INFO HARGA & KALKULATOR PINTAR
# ════════════════════════════════════════════════════════════════

def get_price_for_qty(tiers, quantity):
    if not tiers:
        return 0
    import re
    for tier_key, price in tiers.items():
        key_clean = re.sub(r'(?i)[a-z\s+]+', '', tier_key).strip()
        if '-' in key_clean:
            parts = key_clean.split('-')
            try:
                low = int(parts[0])
                high = int(parts[1])
                if low <= quantity <= high:
                    return price
            except ValueError:
                pass
        elif '>' in key_clean:
            try:
                val = int(key_clean.replace('>', ''))
                if quantity > val:
                    return price
            except ValueError:
                pass
        else:
            try:
                val = int(key_clean)
                if quantity == val:
                    return price
            except ValueError:
                pass
    return list(tiers.values())[-1]


def hitung_harga_item_db(jenis_produk, bahan, qty, panjang=0.0, lebar=0.0):
    from .models import ProductPrice
    
    prod_name = jenis_produk.strip()
    material_name = bahan.strip() if bahan else ''
    
    # 1. Check if it's outdoor banner (priced by m2)
    is_outdoor = any(k in prod_name.lower() for k in ['banner', 'spanduk', 'mmt', 'baliho', 'outdoor', 'albatros', 'oneway', 'one way', 'luster'])
    
    if is_outdoor and panjang > 0 and lebar > 0:
        luas = panjang * lebar
        prod_obj = None
        if material_name:
            prod_obj = ProductPrice.objects.filter(
                kategori='print_outdoor_per_m2',
                nama_produk__icontains=material_name
            ).first()
            if not prod_obj:
                prod_obj = ProductPrice.objects.filter(
                    kategori='print_outdoor_per_m2',
                    material__icontains=material_name
                ).first()
        if not prod_obj:
            prod_obj = ProductPrice.objects.filter(
                kategori='print_outdoor_per_m2',
                nama_produk__icontains=prod_name
            ).first()
        if not prod_obj:
            prod_obj = ProductPrice.objects.filter(
                kategori='print_outdoor_per_m2',
                nama_produk__icontains='280gr'
            ).first()
            
        if prod_obj:
            return int(luas * prod_obj.harga * qty)
        return int(luas * 25000 * qty)
        
    # 2. Check for Sticker A3+
    is_sticker = any(k in prod_name.lower() for k in ['stiker', 'sticker'])
    if is_sticker:
        prod_obj = None
        if material_name:
            prod_obj = ProductPrice.objects.filter(
                kategori='sticker_a3_plus',
                nama_produk__icontains=material_name
            ).first()
        if not prod_obj:
            prod_obj = ProductPrice.objects.filter(
                kategori='sticker_a3_plus',
                nama_produk__icontains='Chromo'
            ).first()
            
        if prod_obj:
            if prod_obj.price_type == 'tiered':
                price_unit = get_price_for_qty(prod_obj.tiers, qty)
            else:
                price_unit = prod_obj.harga
            return int(price_unit * qty)
        return int(7000 * qty)
        
    # 3. Check for Kartu Nama
    is_kartu = any(k in prod_name.lower() for k in ['kartu nama', 'kartu'])
    if is_kartu:
        prod_obj = None
        sisi_name = "2 Sisi" if "2" in prod_name or "2" in material_name else "1 Sisi"
        has_laminasi = any(k in prod_name.lower() or k in material_name.lower() for k in ['laminasi', 'lam', 'glossy', 'doff'])
        search_name = sisi_name + " + Laminasi" if has_laminasi else sisi_name
        
        prod_obj = ProductPrice.objects.filter(
            kategori='kartu_nama_ivory_260',
            nama_produk__icontains=search_name
        ).first()
        
        if prod_obj:
            if prod_obj.price_type == 'tiered':
                price_unit = get_price_for_qty(prod_obj.tiers, qty)
            else:
                price_unit = prod_obj.harga
            return int(price_unit * qty)
        return int(35000 * qty)
        
    # 4. Check for Print A3+
    is_a3 = any(k in prod_name.lower() or k in material_name.lower() for k in ['a3', 'cetak a3', 'print a3'])
    if is_a3:
        prod_obj = None
        paper_types = ['AP150', 'Ivory 230', 'Ivory 260', 'HVS']
        for p_t in paper_types:
            if p_t.lower() in prod_name.lower() or p_t.lower() in material_name.lower():
                prod_obj = ProductPrice.objects.filter(
                    kategori='print_a3_plus',
                    nama_produk__icontains=p_t
                ).first()
                break
        if not prod_obj:
            prod_obj = ProductPrice.objects.filter(
                kategori='print_a3_plus',
                nama_produk__icontains='AP150'
            ).first()
            
        if prod_obj:
            if prod_obj.price_type == 'tiered':
                price_unit = get_price_for_qty(prod_obj.tiers, qty)
            else:
                price_unit = prod_obj.harga
            return int(price_unit * qty)
        return int(5500 * qty)
        
    prod_obj = ProductPrice.objects.filter(nama_produk__icontains=prod_name).first()
    if prod_obj:
        if prod_obj.price_type == 'tiered':
            price_unit = get_price_for_qty(prod_obj.tiers, qty)
        else:
            price_unit = prod_obj.harga
        return int(price_unit * qty)
        
    return 0


def hitung_harga_otomatis(pesan, nama_pelanggan=""):
    from .models import ProductPrice
    import re
    
    p = pesan.lower().strip()
    panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"
    
    # 1. Parse Input (Dimensions & Qty)
    protected = p
    protected = re.sub(r'\ba[3456]\+?\b', ' ', protected)
    protected = re.sub(r'\b[12]\s*sisi\b', ' ', protected)
    protected = re.sub(r'\d+\s*gr\b', ' ', protected)
    protected = re.sub(r'\d+\s*gsm\b', ' ', protected)
    
    dim_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:m|meter|cm)?\s*(?:x|\*|by|kali)\s*(\d+(?:[.,]\d+)?)\s*(?:m|meter|cm)?', protected)
    
    panjang = None
    lebar = None
    unit = "m"
    
    if dim_match:
        try:
            val1 = float(dim_match.group(1).replace(',', '.'))
            val2 = float(dim_match.group(2).replace(',', '.'))
            context_around = p[max(0, dim_match.start()-5):min(len(p), dim_match.end()+10)]
            if 'cm' in context_around or val1 >= 10 or val2 >= 10:
                unit = "cm"
                panjang = val1 / 100.0
                lebar = val2 / 100.0
            else:
                unit = "m"
                panjang = val1
                lebar = val2
        except Exception:
            pass

    string_for_qty = protected
    if dim_match:
        string_for_qty = protected[:dim_match.start()] + " " + protected[dim_match.end():]

    qty = 1
    qty_with_unit = re.search(r'\b(\d+)\s*(?:lbr|lembar|pcs|pc|box|buah|bks|pack|paket|set)\b', string_for_qty)
    if qty_with_unit:
        try:
            qty = int(qty_with_unit.group(1))
        except Exception:
            pass
    else:
        qty_preceded = re.search(r'\b(?:qty|jumlah|sebanyak)\s*[:=]?\s*(\d+)\b', string_for_qty)
        if qty_preceded:
            try:
                qty = int(qty_preceded.group(1))
            except Exception:
                pass
        else:
            numbers = re.findall(r'\b(\d+)\b', string_for_qty)
            if numbers:
                try:
                    qty = int(numbers[0])
                except Exception:
                    pass

    # 2. Identify Product Category
    is_banner = any(k in p for k in ['banner', 'spanduk', 'mmt', 'baliho', 'outdoor', 'albatros', 'oneway', 'one way', 'luster'])
    is_sticker = any(k in p for k in ['stiker', 'sticker', 'chromo', 'vinyl', 'hologram', 'transparan'])
    is_kartu_nama = any(k in p for k in ['kartu nama', 'kartu', 'box'])
    is_a3 = any(k in p for k in ['a3', 'brosur', 'ap150', 'ap120', 'ivory', 'hvs', 'flyer', 'poster', 'print a3'])
    
    is_calc_intent = any(k in p for k in ['hitung', 'kalkulasi', 'kalkulator', 'estimasi'])
    is_price_intent = any(k in p for k in ['harga', 'berapa', 'tarif', 'biaya', 'kisaran', 'rate', 'cost', 'price'])
    
    # If there is a calculation intent or a price query with dimensions/quantities
    has_specs = (panjang and lebar) or (qty > 1 and (is_sticker or is_kartu_nama or is_a3))
    
    if not (is_banner or is_sticker or is_kartu_nama or is_a3) and (is_calc_intent or (is_price_intent and has_specs)):
        if panjang and lebar:
            is_banner = True
        else:
            return (
                f"Tentu {panggilan}! Silakan sebutkan produk yang ingin dihitung harganya.\n"
                f"Contoh:\n"
                f"• _hitung banner 2x3 meter 2 lembar_\n"
                f"• _hitung stiker chromo 50 lembar_\n"
                f"• _hitung kartu nama 1 sisi 3 box_"
            )

    if not (is_banner or is_sticker or is_kartu_nama or is_a3 or is_calc_intent or (is_price_intent and has_specs)):
        return None

    # 3. Perform Calculations and Format Response
    if is_banner:
        if not panjang or not lebar:
            return (
                f"Untuk menghitung harga *Banner/Spanduk*, mohon sertakan ukurannya ya {panggilan}.\n"
                f"Contoh: *hitung banner 3x1 sebanyak 2 lembar*"
            )
            
        luas = panjang * lebar
        lines = [
            f"🧮 *KALKULATOR PINTAR - BANNER/SPANDUK*",
            f"Halo {panggilan}! Berikut rincian estimasi harganya:\n",
            f"📐 *Ukuran*: {panjang:.2f} x {lebar:.2f} meter (Luas: {luas:.2f} m²)",
            f"📦 *Jumlah*: {qty} lembar\n",
            f"💵 *Pilihan Bahan & Estimasi Harga*:"
        ]
        
        products = ProductPrice.objects.filter(kategori='print_outdoor_per_m2')
        if not products.exists():
            products = ProductPrice.objects.filter(kategori__icontains='outdoor')
            
        for prod in products:
            price_per_m2 = prod.harga
            subtotal = int(luas * price_per_m2 * qty)
            lines.append(f"• *{prod.nama_produk}* (Rp {price_per_m2:,}/m²)".replace(',', '.'))
            lines.append(f"  └─ Total: *Rp {subtotal:,}*".replace(',', '.'))
            
        lines.append("\n_Catatan: Harga belum termasuk biaya desain & finishing khusus (jika ada)._")
        lines.append("Mau langsung order? Balas dengan ketik *Order* atau *1* ya Kak! 😊")
        return "\n".join(lines)
        
    elif is_sticker:
        lines = [
            f"🧮 *KALKULATOR PINTAR - STIKER A3+*",
            f"Halo {panggilan}! Berikut rincian estimasi harganya:\n",
            f"📦 *Jumlah*: {qty} lembar A3+\n",
            f"💵 *Pilihan Bahan & Estimasi Harga*:"
        ]
        
        products = ProductPrice.objects.filter(kategori='sticker_a3_plus')
        for prod in products:
            if prod.price_type == 'tiered':
                price_unit = get_price_for_qty(prod.tiers, qty)
            else:
                price_unit = prod.harga
            subtotal = int(price_unit * qty)
            lines.append(f"• *Stiker {prod.nama_produk}*:")
            lines.append(f"  └─ Rp {price_unit:,}/lbr × {qty} lbr = *Rp {subtotal:,}*".replace(',', '.'))
            
        lines.append("\n_Semakin banyak jumlah lembaran, harga per lembar semakin murah!_")
        lines.append("Mau langsung order? Balas dengan ketik *Order* atau *1* ya Kak! 😊")
        return "\n".join(lines)
        
    elif is_kartu_nama:
        lines = [
            f"🧮 *KALKULATOR PINTAR - KARTU NAMA*",
            f"Halo {panggilan}! Berikut rincian estimasi harganya:\n",
            f"📦 *Jumlah*: {qty} Box (1 Box = 100 lembar)\n",
            f"💵 *Pilihan Bahan & Estimasi Harga (Bahan Ivory 260)*:"
        ]
        
        products = ProductPrice.objects.filter(kategori='kartu_nama_ivory_260')
        for prod in products:
            if prod.price_type == 'tiered':
                price_unit = get_price_for_qty(prod.tiers, qty)
            else:
                price_unit = prod.harga
            subtotal = int(price_unit * qty)
            lines.append(f"• *{prod.nama_produk}*:")
            lines.append(f"  └─ Rp {price_unit:,}/box × {qty} box = *Rp {subtotal:,}*".replace(',', '.'))
            
        lines.append("\n_Tersedia juga bahan premium Aster. Silakan hubungi admin jika ingin bahan Aster._")
        lines.append("Mau langsung order? Balas dengan ketik *Order* atau *1* ya Kak! 😊")
        return "\n".join(lines)
        
    elif is_a3:
        lines = [
            f"🧮 *KALKULATOR PINTAR - PRINT A3+*",
            f"Halo {panggilan}! Berikut rincian estimasi harganya:\n",
            f"📦 *Jumlah*: {qty} lembar A3+\n",
            f"💵 *Pilihan Kertas & Estimasi Harga*:"
        ]
        
        products = ProductPrice.objects.filter(kategori='print_a3_plus')
        for prod in products:
            if prod.price_type == 'tiered':
                price_unit = get_price_for_qty(prod.tiers, qty)
            else:
                price_unit = prod.harga
            subtotal = int(price_unit * qty)
            lines.append(f"• *Kertas {prod.nama_produk}*:")
            lines.append(f"  └─ Rp {price_unit:,}/lbr × {qty} lbr = *Rp {subtotal:,}*".replace(',', '.'))
            
        lines.append("\nMau langsung order? Balas dengan ketik *Order* atau *1* ya Kak! 😊")
        return "\n".join(lines)

    return None

def cek_harga(pesan, nama_pelanggan):
    """
    Cek apakah pelanggan menanyakan harga — jika ya, jawab dengan info harga.
    TIDAK mengirimkan form order.
    """
    # 1. Coba hitung harga otomatis dengan kalkulator pintar terlebih dahulu
    jawaban_kalkulator = hitung_harga_otomatis(pesan, nama_pelanggan)
    if jawaban_kalkulator:
        return jawaban_kalkulator

    # 2. Jika tidak ada spesifikasi kalkulator, tampilkan list harga umum
    from .models import ProductPrice
    p = pesan.lower()
    panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

    # Hanya trigger jika ada keyword tanya harga yang eksplisit
    kata_harga = ['harga', 'berapa', 'tarif', 'rate', 'biaya', 'cost', 'price', 'kisaran']
    if not any(k in p for k in kata_harga):
        return None

    # Coba ambil dari DB dulu
    prices = ProductPrice.objects.all()
    if prices.exists():
        # Filter berdasarkan produk yang disebutkan
        kata_produk_map = {
            'banner': ['banner', 'spanduk', 'baliho', 'mmt'],
            'stiker': ['stiker', 'sticker'],
            'kartu nama': ['kartu nama', 'kartu'],
            'brosur': ['brosur', 'flyer', 'poster', 'a3', 'print'],
            'stand banner': ['stand banner', 'x banner', 'roll banner'],
            'buku yasin': ['yasin', 'buku'],
        }

        produk_ditanya = None
        for produk, keywords in kata_produk_map.items():
            if any(k in p for k in keywords):
                produk_ditanya = produk
                break

        if produk_ditanya:
            items = prices.filter(kategori__icontains=produk_ditanya) | prices.filter(nama_produk__icontains=produk_ditanya)
            if items.exists():
                lines = [f"💰 *Info Harga {produk_ditanya.title()}* untuk {panggilan}:\n"]
                for item in items[:8]:
                    lines.append(f"• {item.nama_produk}: *Rp {item.harga:,}*".replace(',', '.'))
                lines.append("\n_Harga bisa berubah tergantung ukuran, bahan, dan jumlah order._")
                lines.append("Mau pesan atau butuh info lebih lanjut? Balas aja ya Kak 😊")
                return "\n".join(lines)

    # Fallback harga hardcoded jika DB kosong
    if any(k in p for k in ['banner', 'spanduk', 'baliho', 'mmt']):
        return (
            f"💰 *Harga Banner/Spanduk* untuk {panggilan}:\n\n"
            f"• Ekonomis (200gr): *Rp 18.000/m²*\n"
            f"• Best (280gr): *Rp 25.000/m²*\n"
            f"• Super (340gr): *Rp 35.000/m²*\n"
            f"• Premium (440gr): *Rp 65.000/m²*\n\n"
            f"_Harga belum termasuk ongkos desain & finishing._\n"
            f"Tertarik pesan? Balas dengan ukuran yang diinginkan ya {panggilan} 😊"
        )
    if any(k in p for k in ['stiker', 'sticker']):
        return (
            f"💰 *Harga Stiker* untuk {panggilan}:\n\n"
            f"• Chromo A3+: *Rp 7.000/lembar*\n"
            f"• Vinyl anti air A3+: *Rp 15.000/lembar*\n"
            f"• Cutting stiker: harga menyesuaikan ukuran\n\n"
            f"Mau pesan berapa lembar {panggilan}? 😊"
        )
    if any(k in p for k in ['kartu nama', 'kartu']):
        return (
            f"💰 *Harga Kartu Nama* untuk {panggilan}:\n\n"
            f"• 1 sisi (Art Carton 260gr): *Rp 35.000/box* (100 lembar)\n"
            f"• 2 sisi: *Rp 50.000/box*\n"
            f"• Laminasi: tambah Rp 15.000/box\n\n"
            f"Mau pesan berapa box {panggilan}? 😊"
        )
    if any(k in p for k in ['brosur', 'flyer', 'poster', 'a3', 'print']):
        return (
            f"💰 *Harga Print A3+* untuk {panggilan}:\n\n"
            f"• Art Paper 150gr: *Rp 5.000/lembar*\n"
            f"• Art Carton 260gr: *Rp 8.000/lembar*\n"
            f"• Laminasi glossy/doff: tambah Rp 3.000/lembar\n\n"
            f"Cetak berapa lembar {panggilan}? 😊"
        )

    # Tanya harga umum tanpa spesifik produk
    return (
        f"Halo {panggilan}! 😊 Berikut kisaran harga kami:\n\n"
        f"🏳️ *Banner/Spanduk*: Rp 18.000–65.000/m²\n"
        f"🏷️ *Stiker*: Rp 7.000–15.000/lembar\n"
        f"💳 *Kartu Nama*: Rp 35.000–65.000/box\n"
        f"📄 *Print A3+*: Rp 5.000–8.000/lembar\n"
        f"🪧 *Stand Banner*: mulai Rp 150.000\n"
        f"📖 *Buku Yasin*: hubungi admin untuk penawaran\n\n"
        f"Tanya harga spesifik produk tertentu? Sebutkan aja ya Kak 😊"
    )


# ════════════════════════════════════════════════════════════════
# FORM ORDER — Dikirim hanya jika pelanggan eksplisit mau order
# ════════════════════════════════════════════════════════════════

def get_form_order(nama_pelanggan=""):
    from .models import SystemConfig
    nama_isi = nama_pelanggan if nama_pelanggan else ""
    biz_name = get_business_name()
    default_template = (
        f"📋 *FORM ORDER - {biz_name}*\n"
        f"_(Bisa isi lebih dari 1 item, copy baris Item 2 dst. jika perlu)_\n\n"
        f"👤 *Data Pemesan*\n"
        f"- Nama    : {nama_isi}\n"
        f"- No. WA  : \n\n"
        f"📦 *Item 1*\n"
        f"- Jenis Produk  : \n"
        f"- Jumlah        : \n"
        f"- Ukuran        : \n"
        f"- Bahan/Material: \n"
        f"- Finishing     : \n"
        f"- File Desain   : *sudah ada* / *belum ada*\n"
        f"- Keterangan    : \n\n"
        f"📦 *Item 2 (isi jika ada, hapus jika tidak perlu)*\n"
        f"- Jenis Produk  : \n"
        f"- Jumlah        : \n"
        f"- Ukuran        : \n"
        f"- Bahan/Material: \n"
        f"- Finishing     : \n"
        f"- File Desain   : *sudah ada* / *belum ada*\n"
        f"- Keterangan    : \n\n"
        f"_ℹ️ Kolom yang tidak relevan isi dengan -*_\n"
        f"_Tambah *Item 3*, *Item 4*, dst. jika ada lebih banyak pesanan._\n\n"
        f"⚠️ *PENTING:* Cukup isi lengkap data di atas dan kirimkan kembali. Pesanan Kakak akan langsung otomatis terdaftar di sistem kami ya! 🙏😊"
    )
    try:
        conf = SystemConfig.objects.get(pk="form_order_template")
        template = conf.value
        if nama_pelanggan and "Nama    : " in template:
            template = template.replace("Nama    : ", f"Nama    : {nama_pelanggan}")
        return template
    except SystemConfig.DoesNotExist:
        return default_template


# ════════════════════════════════════════════════════════════════
# ATURAN AWAL — Lebih cerdas, tidak langsung kirim form
# ════════════════════════════════════════════════════════════════

def cek_rules_awal(pesan, nomor, nama_pelanggan):
    """
    Rules berbasis keyword — dieksekusi sebelum AI.
    Hanya kirim form jika pelanggan EKSPLISIT ingin order.
    Untuk tanya harga/produk → jawab INFO dulu, bukan form.
    """
    p = pesan.lower().strip()
    panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

    # ── SAPAAN ───────────────────────────────────────────────────
    sapaan_list = ['halo', 'p', 'ping', 'hai', 'hi', 'min', 'tes', 'test',
                   'pagi', 'siang', 'sore', 'malam', 'hei', 'permisi', 'selamat', 'assalamualaikum', 'ass']
    if p in sapaan_list or p.startswith('ass') or p.startswith('wass'):
        biz_name = get_business_name()
        if not nama_pelanggan:
            menunggu_nama.add(nomor)
            return (
                f"Halo Kak! Selamat datang di *{biz_name}* ⭐\n"
                "Boleh tahu nama Kakak siapa? 😊"
            )
        return (
            f"Halo {panggilan}! 👋 Selamat datang kembali di {biz_name}.\n\n"
            f"Ada yang bisa kami bantu? Mau:\n"
            f"1. 📋 *Order* produk cetak\n"
            f"2. 💰 *Tanya harga* produk\n"
            f"3. 📦 *Cek status* pesanan\n\n"
            f"Balas angkanya atau langsung ketik pertanyaannya ya Kak 😊"
        )

    # ── MENU ANGKA ───────────────────────────────────────────────
    if p in ['1', '2', '3']:
        if p == '1':
            form = get_form_order(nama_pelanggan)
            return f"Siap {panggilan}! Silakan *copy* dan isi form order berikut:\n\n{form}"
        elif p == '2':
            return cek_harga("harga", nama_pelanggan) or "Produk apa yang ingin ditanyakan harganya?"
        elif p == '3':
            return (
                "Untuk cek status pesanan, kirimkan ID order kakak ya.\n"
                "Contoh: *Cek ORD-20260517-XXXX*\n\n"
                "ID order dikirimkan saat pertama kali pesan masuk. 😊"
            )

    # ── TANYA KATALOG / PRODUK APA SAJA ──────────────────────────
    kata_katalog = ['produk', 'jual apa', 'cetak apa', 'bikin apa', 'katalog',
                    'menu', 'daftar', 'ada apa', 'jenis', 'layanan', 'melayani']
    if any(k in p for k in kata_katalog):
        return (
            f"Kami melayani {panggilan}: 😊\n\n"
            f"🏳️ *Banner / Spanduk* — Ekonomis hingga Premium\n"
            f"🪧 *Stand Banner / X-Banner / Roll Banner*\n"
            f"💳 *Kartu Nama* — 1 sisi & 2 sisi\n"
            f"🏷️ *Stiker* — Chromo & Vinyl anti air\n"
            f"📄 *Print A3+* — Brosur, Poster, Flyer\n"
            f"🎁 *Merchandise* — berbagai item promosi\n"
            f"📖 *Buku Yasin* — cetak partai\n\n"
            f"Tanya harga produk tertentu atau langsung mau pesan? 😊"
        )

    # ── MINTA FORM / EKSPLISIT MAU ORDER ─────────────────────────
    # Trigger 1: kata order eksplisit
    kata_order_eksplisit = [
        'mau order', 'mau pesan', 'ingin order', 'ingin pesan', 'mau buat pesanan',
        'minta form', 'kirim form', 'form order', 'mau daftar', 'daftar pesanan',
        'order sekarang', 'pesan sekarang', 'buat order', 'bikin order',
        'mau nge-order', 'mo order', 'mo pesan',
    ]
    if any(k in p for k in kata_order_eksplisit):
        form = get_form_order(nama_pelanggan)
        return f"Siap {panggilan}! Silakan *copy* dan isi form order berikut:\n\n{form}"

    # Trigger 2: ada kata niat cetak/buat/bikin + nama produk → langsung form
    kata_niat = ['mau cetak', 'mau bikin', 'mau buat', 'pengen cetak', 'pengen bikin',
                 'butuh cetak', 'perlu cetak', 'cetak dong', 'bikin dong', 'mau print',
                 'mau ngeprint', 'butuh print', 'ingin cetak', 'ingin bikin']
    kata_produk_all = ['banner', 'spanduk', 'baliho', 'mmt', 'stiker', 'sticker',
                       'kartu nama', 'brosur', 'poster', 'flyer', 'merchandise',
                       'buku yasin', 'yasin', 'stand banner', 'x banner', 'roll banner']

    punya_niat  = any(k in p for k in kata_niat)
    punya_produk = any(k in p for k in kata_produk_all)

    if punya_niat and punya_produk:
        form = get_form_order(nama_pelanggan)
        return f"Siap {panggilan}! Silakan *copy* dan isi form order berikut:\n\n{form}"

    # Trigger 3: hanya sebutkan produk TANPA tanya harga → tawarkan opsi
    # (jangan langsung form — mungkin mereka hanya tanya info)
    if punya_produk and not any(k in p for k in ['harga', 'berapa', 'tarif', 'biaya']):
        return (
            f"Halo {panggilan}! 😊 Butuh bantuan apa untuk *{pesan.strip()}*?\n\n"
            f"1️⃣ Tanya *harga*\n"
            f"2️⃣ Langsung *order / pesan*\n"
            f"3️⃣ Tanya *info* produk\n\n"
            f"Balas angkanya ya Kak 🙏"
        )

    return None


# ════════════════════════════════════════════════════════════════
# FAQ dari Database
# ════════════════════════════════════════════════════════════════

def cek_database_faq(pesan, nama_pelanggan):
    from .models import FAQ
    faqs = FAQ.objects.all()
    if not faqs.exists():
        return None
    db_faq = {f.pertanyaan: f.jawaban for f in faqs}
    mirip = difflib.get_close_matches(pesan.lower().strip(), list(db_faq.keys()), n=1, cutoff=0.8)
    if mirip:
        jawaban = db_faq[mirip[0]]
        if nama_pelanggan:
            jawaban = (jawaban
                       .replace("Kak!", f"Kak {nama_pelanggan}!")
                       .replace("Kak.", f"Kak {nama_pelanggan}."))
        return jawaban
    return None


# ════════════════════════════════════════════════════════════════
# AI FALLBACK
# ════════════════════════════════════════════════════════════════

def tanya_ai_finishing(nomor):
    try:
        from openai import OpenAIError
    except ImportError:
        OpenAIError = Exception

    try:
        history = get_memori_percakapan(nomor)
        client = get_ai_client()
        
        # Retry logic with exponential backoff and timeout handling
        import time
        max_retries = 3
        backoff_sec = 1.0
        response = None
        
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model="gemini-2.5-flash",
                    messages=history,
                    max_tokens=350,
                    temperature=0.3,
                    timeout=15.0,  # 15 seconds timeout
                )
                break
            except Exception as e:
                logger.warning(f"AI completion attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise e
                time.sleep(backoff_sec)
                backoff_sec *= 2.0

        if not response or not hasattr(response, 'choices') or not response.choices:
            raise ValueError("No completion choices returned from AI model")
        
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty response content returned from AI model")
            
        return content
    except ValueError as e:
        logger.error(f"[ERROR AI Webhook] Invalid response structure: {e}")
        return "Halo Kak! Mohon maaf, respon AI kami sedang kosong. Bisa diulangi pertanyaannya? 🙏😊"
    except OpenAIError as e:
        logger.error(f"[ERROR AI Webhook] OpenAI API error occurred: {e}", exc_info=True)
        return "Halo Kak! Mohon maaf, koneksi ke asisten virtual kami terganggu. Silakan dicoba lagi sebentar lagi ya... 🙏😊"
    except Exception as e:
        logger.error(f"[ERROR AI Webhook] Unexpected error: {e}", exc_info=True)
        return (
            "Halo Kak! Mohon maaf sistem kami sedang sedikit sibuk. "
            "Boleh diulangi dalam 1-2 menit? 🙏😊"
        )
