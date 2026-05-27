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
        base_url="https://api.koboillm.com/v1"
    )

# ── State in-memory ───────────────────────────────────────────────────
memori_percakapan = {}
menunggu_nama     = set()


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
    from .models import ProductPrice, AppConfig
    import json

    prices = ProductPrice.objects.all()
    data_harga = {}
    for p in prices:
        if p.kategori not in data_harga:
            data_harga[p.kategori] = {}
        data_harga[p.kategori][p.nama_produk] = p.harga
    string_harga = json.dumps(data_harga, ensure_ascii=False, indent=2)

    try:
        conf = AppConfig.objects.get(pk="system_prompt")
        template_ai = conf.value
    except AppConfig.DoesNotExist:
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
            "⚠️ *PENTING:* Setelah form diisi, tambahkan di baris paling bawah:\n"
            "*DATA SUDAH SESUAI*\n"
            "agar pesanan otomatis masuk ke sistem kami.\n"
            "=== AKHIR TEMPLATE ===\n\n"
            "Saat mengirim form, awali dengan: 'Siap Kak! Silakan *copy* dan isi form berikut:'"
        )

    return f"{template_ai}\n\nData harga produk:\n{string_harga}\n"


def simpan_ke_memori(nomor, role, konten, nama_pelanggan=""):
    if nomor not in memori_percakapan:
        memori_percakapan[nomor] = [{"role": "system", "content": get_system_prompt(nama_pelanggan)}]
    else:
        memori_percakapan[nomor][0]['content'] = get_system_prompt(nama_pelanggan)
    memori_percakapan[nomor].append({"role": role, "content": konten})
    if len(memori_percakapan[nomor]) > 11:
        memori_percakapan[nomor] = [memori_percakapan[nomor][0]] + memori_percakapan[nomor][-10:]


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
    lines = [
        f"📦 *STATUS PESANAN ({order.id})*",
        f"👤 *Pemesan*: {order.nama or '-'}",
        f"📋 *Status*: {order.status_global.upper()}",
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

    lines.append(
        f"\n_Pesanan sudah kami catat {panggilan}. "
        f"Tim kami akan segera memverifikasi dan menghubungi Kakak. Mohon ditunggu ya! 🙏_"
    )
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


# ════════════════════════════════════════════════════════════════
# INFO HARGA — Jawab tanpa langsung kirim form order
# ════════════════════════════════════════════════════════════════

def cek_harga(pesan, nama_pelanggan):
    """
    Cek apakah pelanggan menanyakan harga — jika ya, jawab dengan info harga.
    TIDAK mengirimkan form order.
    """
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
    from .models import AppConfig
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
        f"⚠️ *PENTING:* Setelah form diisi lengkap, tambahkan:\n"
        f"*DATA SUDAH SESUAI*\n"
        f"di baris paling bawah agar pesanan otomatis masuk ke sistem kami 👇"
    )
    try:
        conf = AppConfig.objects.get(pk="form_order_template")
        template = conf.value
        if nama_pelanggan and "Nama    : " in template:
            template = template.replace("Nama    : ", f"Nama    : {nama_pelanggan}")
        return template
    except AppConfig.DoesNotExist:
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
        client = get_ai_client()
        response = client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=memori_percakapan.get(nomor, []),
            max_tokens=350,
            temperature=0.3,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ Error AI: {e}")
        return (
            "Halo Kak! Mohon maaf sistem kami sedang sedikit sibuk. "
            "Boleh diulangi dalam 1-2 menit? 🙏😊"
        )
