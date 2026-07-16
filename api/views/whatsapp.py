import logging
import os
import uuid
import re as _re
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache

from ..models import (
    Contact, Order, OrderItem, JobBoard, TahapProses, SystemConfig, CustomUser
)
from ..permissions import (
    IsOwnerOrManager, IsOwnerManagerAdminOrKasir
)
from ..whatsapp_client import whatsapp_client

logger = logging.getLogger(__name__)


def update_desain_dari_form_data(detail):
    """
    Parse teks FORM KONSEP DESAIN -> update ke DB pada OrderItem yang cocok.
    Kembalikan (order_id, success_boolean, error_message).
    """
    def ambil_field(teks, *keys):
        keys_all = [
            'ID Pesanan', 'ID Order',
            'Tulisan yang dimuat', 'Tulisan',
            'Dominan Warna', 'Warna',
            'Logo / Foto (Ada/Tidak)', 'Logo / Foto', 'Logo',
            'Bentuk (Vertikal / Horizontal)', 'Bentuk',
            'Request Tambahan', 'Keterangan',
            'desain sudah sesuai', 'data sudah sesuai',
            'File Desain'
        ]
        escaped_keys = []
        for k in keys_all:
            pat = _re.escape(k).replace('\\ ', ' ').replace(' ', '[ \t\xa0]+')
            escaped_keys.append(pat)
        
        # Add footers manually
        escaped_keys.append(r'===?\s*AKHIR\s*TEMPLATE\s*===?')
        escaped_keys.append(r'⚠️\s*\*?PENTING:\*?')
        
        lookahead_keys_pat = "|".join(escaped_keys)

        for key in keys:
            key_pat = _re.escape(key).replace('\\ ', ' ').replace(' ', '[ \t\xa0]+')
            # Lookahead check for next field header or footer keywords
            pattern = rf'(?:[-*••]|\d+\.)?[ \t\xa0]*{key_pat}[ \t\xa0]*[:=][ \t\xa0]*(.*?)(?=\r?\n[ \t\xa0]*(?:[-*••]|\d+\.)?[ \t\xa0]*(?:{lookahead_keys_pat})[ \t\xa0]*[:=]|\r?\n[ \t\xa0]*(?:{lookahead_keys_pat})|$)'
            match = _re.search(pattern, teks, _re.IGNORECASE | _re.DOTALL)
            if match:
                val = match.group(1).strip().strip('*_')
                if val and val not in ('-', 'sudah ada / belum ada', '*sudah ada* / *belum ada*'):
                    return val
        return ''

    order_id = ambil_field(detail, 'ID Pesanan', 'ID Order').upper()
    if not order_id:
        # Fallback search menggunakan regex untuk ORD-...
        match = _re.search(r'(ord-[\w-]+)', detail, _re.IGNORECASE)
        if match:
            order_id = match.group(1).upper()

    if not order_id:
        return None, False, "ID Pesanan tidak ditemukan dalam form konsep desain Kakak."

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        return order_id, False, f"Pesanan dengan ID *{order_id}* tidak ditemukan di sistem kami."

    # Ekstrak field konsep desain
    tulisan = ambil_field(detail, 'Tulisan yang dimuat', 'Tulisan')
    warna = ambil_field(detail, 'Dominan Warna', 'Warna')
    logo = ambil_field(detail, 'Logo / Foto (Ada/Tidak)', 'Logo / Foto', 'Logo')
    bentuk = ambil_field(detail, 'Bentuk (Vertikal / Horizontal)', 'Bentuk')
    req_tambahan = ambil_field(detail, 'Request Tambahan', 'Keterangan')

    # Update OrderItem milik order ini
    item = order.items.first()
    if not item:
        return order_id, False, f"Pesanan *{order_id}* tidak memiliki item produk."

    with transaction.atomic():
        # Bentuk detail_desain dalam JSON
        detail_json = []
        if isinstance(item.detail, list):
            detail_json = item.detail
        elif isinstance(item.detail, str) and item.detail:
            detail_json = [{"key": "Spesifikasi", "value": item.detail}]

        # Ganti info konsep desain lama jika ada
        detail_json = [d for d in detail_json if d.get('key') != 'Konsep Desain']
        detail_json.append({
            "key": "Konsep Desain",
            "value": {
                "tulisan": tulisan,
                "warna_dominan": warna,
                "logo_foto": logo,
                "bentuk": bentuk,
                "request_tambahan": req_tambahan
            }
        })
        item.detail = detail_json
        
        # Update keterangan_detail
        keterangan_gabung = (
            f"Konsep Desain:\n"
            f"- Tulisan yang dimuat: {tulisan}\n"
            f"- Dominan Warna: {warna}\n"
            f"- Logo / Foto (Ada/Tidak): {logo}\n"
            f"- Bentuk (Vertikal / Horizontal): {bentuk}\n"
            f"- Request Tambahan: {req_tambahan}"
        )
        item.keterangan_detail = keterangan_gabung
        item.save()

        # Pindahkan job board to tahap desain jika ada
        jobs = JobBoard.objects.filter(order_item=item)
        for job in jobs:
            if job.tahap and 'desain' in job.tahap.nama.lower():
                # Jika status pekerjaan tertahan di antrean, aktifkan kembali
                job.status_pekerjaan = 'antrean'
                job.save()

    return order_id, True, ""


class FonnteWebhookView(APIView):
    """
    Endpoint webhook dari Fonnte. Tidak pakai JWT (AllowAny).
    Alur: tanya nama → tracking → form order → aturan awal → FAQ → AI
    """
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def _kirim_balas(self, pesan):
        """Selalu kembalikan format array replies yang diharapkan Fonnte."""
        return Response({'replies': [{'message': pesan}]}, status=status.HTTP_200_OK)

    def _reply_kosong(self):
        """Untuk pesan yang diabaikan — tetap return replies array kosong."""
        return Response({'replies': []}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        # Validasi Shared Secret Token (fail-closed jika tidak dikonfigurasi)
        expected_secret = os.getenv("FONNTE_WEBHOOK_SECRET")
        if not expected_secret:
            logger.error("FONNTE_WEBHOOK_SECRET is not configured. Webhook is closed.")
            return Response({'error': 'Webhook secret not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        token = request.query_params.get("secret") or request.headers.get("X-Fonnte-Secret")
        if not token or token != expected_secret:
            logger.warning(f"Unauthorized Webhook Fonnte call with token: {token}")
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        from ..wa_logic import (
            menunggu_nama,
            simpan_ke_memori, cek_tracking, cek_harga, cek_rules_awal,
            cek_database_faq, tanya_ai_finishing, ekstrak_nama_dari_pesan,
            proses_kirim_desain,
        )

        data = request.data

        # Fonnte kadang kirim data nested di bawah key 'query'
        if 'query' in data and isinstance(data['query'], dict):
            sender  = str(data['query'].get('sender', '')).strip()
            message = str(data['query'].get('message', '')).strip()
            is_group = data['query'].get('isGroup', False)
            media_url = data['query'].get('url', '') or data['query'].get('filename', '')
        else:
            sender  = str(data.get('sender', '')).strip()
            message = str(data.get('message', data.get('query', ''))).strip()
            is_group = False
            media_url = data.get('url', '') or data.get('filename', '')

        # Bersihkan format nomor: hapus +, spasi, tanda -
        # Contoh: "+62 882-0075-63131" → "628820075631131"
        sender_bersih = sender.replace('+', '').replace(' ', '').replace('-', '')

        if not message or not sender_bersih:
            return self._reply_kosong()

        # Abaikan pesan dari grup WhatsApp
        if is_group or '@g.us' in sender:
            return self._reply_kosong()

        sender = sender_bersih  # pakai nomor yang sudah bersih

        # Ambil kontak dari DB
        contact_obj    = Contact.objects.filter(nomor_wa=sender).first()
        nama_pelanggan = contact_obj.nama if contact_obj else ""
        p_kecil        = message.lower()
        panggilan      = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

        # Cek status Human Handover (aktif 15 menit jika staff membalas chat, atau jika diset manual di DB)
        if cache.get(f"wa_handover_{sender}") or (contact_obj and getattr(contact_obj, 'handover_to_staff', False)):
            logger.info(f"Chat dengan {sender} sedang dalam mode Human Handover (Fonnte). Bot diabaikan.")
            return self._reply_kosong()

        jawaban = ""

        # ── STEP 1: Tanya nama (kontak baru) ──────────────────────
        if not nama_pelanggan and sender not in menunggu_nama:
            menunggu_nama.add(sender)
            try:
                biz_name = SystemConfig.objects.get(key='bisnis_nama').value or 'Brandy'
            except Exception as e:
                logger.warning(f"Gagal mengambil nama bisnis: {e}")
                biz_name = 'Brandy'
            jawaban = (
                f"Halo Kak! 👋 Selamat datang di *{biz_name}*.\n"
                "Boleh tahu dengan Kakak siapa ini biar lebih enak ngobrolnya? 😊"
            )
            return self._kirim_balas(jawaban)

        elif sender in menunggu_nama:
            # Ekstrak nama bersih dari jawaban (bisa berupa kalimat)
            nama_baru = ekstrak_nama_dari_pesan(message)
            contact_obj, _ = Contact.objects.get_or_create(
                nomor_wa=sender, defaults={'nama': nama_baru}
            )
            if not contact_obj.nama:
                contact_obj.nama = nama_baru
                contact_obj.save()
            elif contact_obj.nama != nama_baru:
                contact_obj.nama = nama_baru
                contact_obj.save()
            menunggu_nama.discard(sender)
            nama_pelanggan = nama_baru
            panggilan      = f"Kak {nama_pelanggan}"
            jawaban = (
                f"Salam kenal {panggilan}! ✨\n"
                f"Ada yang bisa kami bantu hari ini? Mau cetak apa nih Kak?"
            )
            return self._kirim_balas(jawaban)

        # Simpan ke memori AI
        simpan_ke_memori(sender, "user", message, nama_pelanggan)

        # ── STEP 2: Tracking / Kirim Desain pesanan ───────────────────────────────
        is_form_order = (
            ('jenis produk' in p_kecil and ('no. wa' in p_kecil or 'item 1' in p_kecil or 'no wa' in p_kecil))
            or
            ('nama pemesan' in p_kecil and 'jenis produk' in p_kecil)
        )
        is_form_desain = 'tulisan yang dimuat' in p_kecil or 'dominan warna' in p_kecil

        jawaban = ""
        if not (is_form_order or is_form_desain):
            jawaban = proses_kirim_desain(message, sender, nama_pelanggan, media_url=media_url)
            if not jawaban:
                jawaban = cek_tracking(message, sender, nama_pelanggan)
            if jawaban:
                # Langsung balas tracking / kirim desain — jangan lanjut ke step lain
                simpan_ke_memori(sender, "assistant", jawaban, nama_pelanggan)
                return self._kirim_balas(jawaban)

        # ── STEP 3: Deteksi form order / desain masuk ─────────────
        if not jawaban:
            # Deteksi form order (template lama & baru)
            is_form_order = (
                # Template baru: ada "jenis produk" + "no. wa" atau "item 1"
                ('jenis produk' in p_kecil and ('no. wa' in p_kecil or 'item 1' in p_kecil or 'no wa' in p_kecil))
                or
                # Template lama: ada "nama pemesan" + "jenis produk"
                ('nama pemesan' in p_kecil and 'jenis produk' in p_kecil)
            )
            is_form_desain = 'tulisan yang dimuat' in p_kecil or 'dominan warna' in p_kecil

            if is_form_order or is_form_desain:
                # Bersihkan footer instruksi/konfirmasi
                detail_bersih = _re.split(r'(?i)===?\s*AKHIR\s*TEMPLATE\s*===?|⚠️\s*\*?PENTING:\*?|data\s+sudah\s+sesuai|desain\s+sudah\s+sesuai', message)[0].strip()
                try:
                    if is_form_desain:
                        order_id, success, err_msg = update_desain_dari_form_data(detail_bersih)
                        if success:
                            jawaban = (
                                f"Terima kasih {panggilan}! Konsep desain sudah masuk ke Antrean Desain ✅\n\n"
                                f"🎫 *ID PESANAN: {order_id}*\n"
                                f"Tim desain kami akan segera memproses konsep Kakak. Mohon ditunggu ya! 🙏"
                            )
                        else:
                            jawaban = (
                                f"Maaf {panggilan}, gagal memproses konsep desain Kakak:\n"
                                f"⚠️ {err_msg}\n\n"
                                f"Mohon periksa kembali ID Pesanan Kakak dan kirimkan ulang dengan benar ya Kak. 🙏"
                            )
                    else:
                        order_id, is_desain_ready = self._simpan_order_dari_form(sender, nama_pelanggan, detail_bersih)
                        order_instance = Order.objects.prefetch_related('items').get(id=order_id)
                        label = "Pesanan Anda telah masuk ke sistem kami"
                        
                        item_lines = []
                        total_estimasi = 0
                        for item in order_instance.items.all():
                            spec_parts = []
                            if item.panjang > 0 and item.lebar > 0:
                                spec_parts.append(f"{item.panjang:.1f}x{item.lebar:.1f}m")
                            if item.bahan:
                                spec_parts.append(item.bahan)
                            spec_str = f" ({', '.join(spec_parts)})" if spec_parts else ""
                            
                            price_val = item.harga_jual or 0
                            price_display = f"Rp {price_val:,}" if price_val > 0 else "Hubungi Admin"
                            price_display = price_display.replace(',', '.')
                            item_lines.append(f"• *{item.jenis_produk}*{spec_str} - {item.qty}x")
                            item_lines.append(f"  └─ Est. Harga: *{price_display}*")
                            total_estimasi += price_val
                            
                        total_display = f"Rp {total_estimasi:,}" if total_estimasi > 0 else "Hubungi Admin"
                        total_display = total_display.replace(',', '.')
                        
                        jawaban = (
                            f"Terima kasih {panggilan}! {label} ✅\n\n"
                            f"🎫 *ID PESANAN: {order_id}*\n"
                            f"_Simpan ID ini untuk melacak status pesanan Kakak._\n\n"
                            f"📝 *RINCIAN PESANAN:*\n"
                            + "\n".join(item_lines) + "\n\n"
                            f"💰 *TOTAL ESTIMASI: {total_display}*\n\n"
                            f"Tim kami akan segera memverifikasi pesanan Kakak. Mohon ditunggu 🙏"
                        )
                        # Jika ada item yang belum memiliki desain, infokan link upload / petunjuk kirim langsung
                        from django.db.models import Q
                        has_no_design = order_instance.items.filter(Q(gdrive_customer_link__isnull=True) | Q(gdrive_customer_link='')).exists()
                        if has_no_design:
                            if is_desain_ready:
                                jawaban += (
                                    f"\n\nSilakan kirimkan file desain Kakak langsung ke chat ini (sebagai Gambar atau Dokumen) "
                                    f"dengan mencantumkan keterangan/caption ID Pesanan: *{order_id}* pada file tersebut ya Kak! 😊"
                                )
                            else:
                                jawaban += (
                                    f"\n\nSilakan *copy-paste* dan isi **Form Konsep Desain** di bawah ini agar tim desainer kami bisa langsung memprosesnya:\n\n"
                                    f"📋 *FORM KONSEP DESAIN*\n"
                                    f"- ID Pesanan: {order_id}\n"
                                    f"- Tulisan yang dimuat:\n"
                                    f"- Dominan Warna:\n"
                                    f"- Logo / Foto (Ada/Tidak):\n"
                                    f"- Bentuk (Vertikal / Horizontal):\n"
                                    f"- Request Tambahan:\n\n"
                                    f"⚠️ *PENTING:* Setelah form diisi lengkap, tambahkan di baris paling bawah:\n"
                                    f"*DESAIN SUDAH SESUAI*\n"
                                    f"agar konsep desain otomatis masuk ke sistem kami. 👇"
                                )
                except ValueError as ve:
                    jawaban = (
                        f"Maaf {panggilan}, format pengisian form pesanan Kakak belum lengkap / ada yang salah:\n\n"
                        f"⚠️ {str(ve)}\n\n"
                        f"Mohon perbaiki dan kirimkan ulang dengan format yang benar ya Kak. 🙏😊"
                    )

        # ── STEP 4: Cek tanya harga (jawab info harga, TANPA form) ──
        if not jawaban:
            jawaban = cek_harga(message, nama_pelanggan)

        # ── STEP 5: Aturan awal (sapaan, katalog, minta form) ─────
        if not jawaban:
            jawaban = cek_rules_awal(message, sender, nama_pelanggan)

        # ── STEP 6: FAQ dari database ──────────────────────────────
        if not jawaban:
            jawaban = cek_database_faq(message, nama_pelanggan)

        # ── STEP 7: AI Fallback (KoboiLLM / Gemini) ───────────────
        if not jawaban:
            jawaban = tanya_ai_finishing(sender)

        simpan_ke_memori(sender, "assistant", jawaban, nama_pelanggan)
        return self._kirim_balas(jawaban)

    def _simpan_order_dari_form(self, nomor, nama_kontak, detail):
        """
        Parse teks form WA → simpan ke DB.
        Support multi-item: tiap blok 'Item N' jadi 1 OrderItem terpisah.
        Kembalikan order_id.
        """

        def ambil_field(teks, *keys, max_len=1000):
            keys_all = [
                'Nama Pemesan', 'Nama', 'No. WA', 'No WA',
                'Jenis Produk', 'Jumlah', 'Ukuran',
                'Bahan/Material', 'Bahan / Material', 'Bahan',
                'Finishing', 'File Desain', 'Keterangan',
                'data sudah sesuai', 'desain sudah sesuai'
            ]
            escaped_keys = []
            for k in keys_all:
                pat = _re.escape(k).replace('\\ ', ' ').replace(' ', '[ \t\xa0]+')
                escaped_keys.append(pat)
            
            escaped_keys.append(r'===?\s*AKHIR\s*TEMPLATE\s*===?')
            escaped_keys.append(r'⚠️\s*\*?PENTING:\*?')
            lookahead_keys_pat = "|".join(escaped_keys)

            for key in keys:
                key_pat = _re.escape(key).replace('\\ ', ' ').replace(' ', '[ \t\xa0]+')
                pattern = rf'(?:[-*••]|\d+\.)?[ \t\xa0]*{key_pat}[ \t\xa0]*[:=][ \t\xa0]*(.*?)(?=\r?\n[ \t\xa0]*(?:[-*••]|\d+\.)?[ \t\xa0]*(?:{lookahead_keys_pat})[ \t\xa0]*[:=]|\r?\n[ \t\xa0]*(?:{lookahead_keys_pat})|$)'
                match = _re.search(pattern, teks, _re.IGNORECASE | _re.DOTALL)
                if match:
                    val = match.group(1).strip().strip('*_')
                    if val and val not in ('-', 'sudah ada / belum ada', '*sudah ada* / *belum ada*'):
                        val = _re.sub(r'<[^>]*>', '', val)
                        return val[:max_len].strip()
            return ''

        # Ambil nama pemesan dari form (bisa "Nama Pemesan" atau "Nama")
        nama_dari_form = (
            ambil_field(detail, 'Nama Pemesan', 'Nama') or nama_kontak or '-'
        )

        # ── Pisah per blok item ──────────────────────────────────
        # Cari semua penanda item: "Item 1", "Item 2", dst.
        # Regex tidak bergantung emoji agar lebih reliable
        blok_items = _re.split(r'(?im)^[ \t]*[-*•\[\*_]*\s*(?:📦\s*)?[\*_]*item\s+\d+[\*_\]:]*[ \t]*[\*_]*[^\r\n]*$', detail)
        blok_items = [b.strip() for b in blok_items if b.strip()]

        if len(blok_items) <= 1:
            # Tidak ada penanda item → seluruh teks jadi 1 item
            blok_items = [detail]

        # Tentukan nama dari kontak
        nama_order = nama_dari_form

        with transaction.atomic():
            contact, _ = Contact.objects.get_or_create(
                nomor_wa=nomor, defaults={'nama': nama_kontak}
            )
            # BUG FIX: last_order adalah DateField, gunakan localdate() bukan strftime dengan jam:menit
            existing_orders = Order.objects.filter(nomor_wa=nomor)
            contact.total_order = existing_orders.count() + 1
            contact.total_spent = sum(
                item.harga_jual
                for o in existing_orders.prefetch_related('items')
                for item in o.items.all()
            )
            contact.last_order  = timezone.localdate()
            contact.save()

            order_id = f"ORD-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
            order = Order.objects.create(
                id=order_id,
                nomor_wa=contact.nomor_wa,  # BUG FIX: harus string, bukan objek Contact
                nama=nama_order,
                status_global='review',
                sumber='wa',
                catatan_pelanggan=detail[:500],
            )

            items_dibuat = 0
            is_desain_ready = False
            for blok in blok_items:
                if not blok.strip():
                    continue

                jenis_produk = ambil_field(blok, 'Jenis Produk', max_len=100) or 'Umum'
                jumlah_str   = ambil_field(blok, 'Jumlah', max_len=50)
                ukuran       = ambil_field(blok, 'Ukuran', max_len=100)
                bahan        = ambil_field(blok, 'Bahan/Material', 'Bahan / Material', 'Bahan', max_len=100)
                finishing    = ambil_field(blok, 'Finishing', max_len=250)
                file_desain  = ambil_field(blok, 'File Desain', max_len=250).lower()
                if file_desain and 'belum' not in file_desain:
                    is_desain_ready = True
                keterangan   = ambil_field(blok, 'Keterangan', max_len=1000)

                # Skip blok item kosong (Item 2 yang tidak diisi)
                if jenis_produk == 'Umum' and not ukuran and not bahan:
                    continue

                try:
                    # Extract the first consecutive block of digits instead of filtering out all non-digits (which turns "2.5" into "25")
                    match_qty = _re.search(r'\d+', jumlah_str or '')
                    qty = int(match_qty.group(0)) if match_qty else 1
                except Exception as e:
                    logger.warning(f"Gagal mem-parse qty '{jumlah_str}', fallback ke 1: {e}")
                    qty = 1

                # Parse panjang & lebar
                panjang = 0.0
                lebar = 0.0
                if ukuran:
                    dimensi_match = _re.search(r'([\d.,]+)\s*[xX*]\s*([\d.,]+)', ukuran)
                    if dimensi_match:
                        try:
                            panjang = float(dimensi_match.group(1).replace(',', '.'))
                            lebar = float(dimensi_match.group(2).replace(',', '.'))
                        except ValueError as e:
                            logger.warning(f"Gagal mem-parse dimensi panjang/lebar dari '{ukuran}': {e}")

                detail_json = []
                if ukuran: detail_json.append({"key": "Ukuran", "value": ukuran})
                if finishing: detail_json.append({"key": "Finishing", "value": finishing})
                if bahan: detail_json.append({"key": "Bahan", "value": bahan})

                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk=jenis_produk,
                    qty=qty,
                    panjang=panjang,
                    lebar=lebar,
                    bahan=bahan or '',
                    harga_jual=0,
                    detail=detail_json,
                    keterangan_detail=keterangan or '',
                    gdrive_customer_link='',
                )

                # Tentukan tahap awal
                if 'belum' in file_desain:
                    tahap_awal = TahapProses.objects.filter(
                        nama__icontains='desain'
                    ).order_by('urutan').first()
                else:
                    tahap_awal = TahapProses.objects.order_by('urutan').first()

                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )
                items_dibuat += 1

            # Jika tidak ada item yang terdeteksi, buat 1 item generik
            if items_dibuat == 0:
                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk='Umum',
                    qty=1,
                    harga_jual=0,
                    detail=detail[:200],
                )
                tahap_awal = TahapProses.objects.order_by('urutan').first()
                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )

        return order_id, is_desain_ready


class EvolutionWebhookView(APIView):
    """
    Webhook endpoint untuk Evolution API. AllowAny.
    Memproses pesan masuk, mendeteksi anti-duplikasi, mengeksekusi logika wa_logic,
    dan mengirim balasan asinkron via REST API Client.
    """
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def post(self, request, *args, **kwargs):
        from ..wa_logic import (
            menunggu_nama,
            simpan_ke_memori, cek_tracking, cek_harga, cek_rules_awal,
            cek_database_faq, tanya_ai_finishing, ekstrak_nama_dari_pesan,
            proses_kirim_desain,
        )

        data = request.data

        # 1. Validasi API Key (fail-closed jika tidak dikonfigurasi)
        expected_key = os.getenv("EVOLUTION_API_KEY")
        if not expected_key:
            logger.error("EVOLUTION_API_KEY is not configured. Webhook is closed.")
            return Response({'error': 'Evolution API key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        from django.utils.crypto import constant_time_compare
        auth_key = request.headers.get("apikey") or request.headers.get("Authorization", "")
        
        is_valid = False
        if auth_key:
            if constant_time_compare(auth_key, expected_key):
                is_valid = True
            elif auth_key.startswith("Bearer ") and constant_time_compare(auth_key, f"Bearer {expected_key}"):
                is_valid = True

        if not is_valid:
            logger.warning(f"Unauthorized Webhook request dengan apikey: {auth_key}")
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        # Hanya proses event messages.upsert
        event_type = data.get('event')
        if event_type and event_type != "messages.upsert":
            return Response({'status': 'ignored_event_type', 'event': event_type}, status=status.HTTP_200_OK)

        event_data = data.get('data', {})
        if not event_data:
            return Response({'error': 'No data payload found'}, status=status.HTTP_400_BAD_REQUEST)

        key = event_data.get('key', {})
        from_me = key.get('fromMe', False)
        if from_me:
            sender = key.get('remoteJid', '')
            if sender and '@g.us' not in sender:
                sender_number = sender.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
                if sender_number:
                    try:
                        # Set database handover_to_staff = True
                        contact_obj, _ = Contact.objects.get_or_create(nomor_wa=sender_number, defaults={'nama': 'Pelanggan'})
                        if not contact_obj.handover_to_staff:
                            contact_obj.handover_to_staff = True
                            contact_obj.save()
                        
                        cache.set(f"wa_handover_{sender_number}", True, timeout=900)
                        logger.info(f"Human takeover detected! Set handover_to_staff=True in DB and cache for {sender_number}.")
                    except Exception as e:
                        logger.error(f"Gagal mengeset handover pada human takeover: {e}")
            return Response({'status': 'ignored_from_me'}, status=status.HTTP_200_OK)

        sender = key.get('remoteJid', '')
        if not sender or '@g.us' in sender:
            logger.info(f"Group message from {sender} ignored.")
            return Response({'status': 'ignored_group_message'}, status=status.HTTP_200_OK)

        sender_number = sender.split('@')[0].replace('+', '').replace(' ', '').replace('-', '')
        if not sender_number:
            return Response({'error': 'No sender number found'}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Inbound Deduplication (Anti-Duplikasi Masuk)
        message_id = key.get('id', '')
        if message_id:
            inbound_cache_key = f"evo_inbound_{message_id}"
            if cache.get(inbound_cache_key):
                logger.info(f"Duplicate inbound message ID {message_id} diabaikan.")
                return Response({'status': 'duplicate_ignored'}, status=status.HTTP_200_OK)
            cache.set(inbound_cache_key, True, timeout=300) # 5 menit TTL

        # Ekstrak konten pesan
        msg_content = event_data.get('message', {})
        message_text = ""
        media_url = ""
        if isinstance(msg_content, dict):
            message_text = (
                msg_content.get('conversation', '') or
                msg_content.get('extendedTextMessage', {}).get('text', '') or
                msg_content.get('imageMessage', {}).get('caption', '') or
                msg_content.get('videoMessage', {}).get('caption', '') or
                msg_content.get('documentMessage', {}).get('caption', '') or
                ''
            ).strip()
            
            # Extract media URL/filename
            if 'imageMessage' in msg_content:
                media_url = msg_content['imageMessage'].get('url', '')
            elif 'videoMessage' in msg_content:
                media_url = msg_content['videoMessage'].get('url', '')
            elif 'documentMessage' in msg_content:
                media_url = msg_content['documentMessage'].get('url', '')
        elif isinstance(msg_content, str):
            message_text = msg_content.strip()

        if not message_text:
            return Response({'status': 'ignored_empty_message'}, status=status.HTTP_200_OK)

        # Check if the sender is a staff member submitting attendance reason
        cleaned_sender = sender_number.lstrip('+').lstrip('0')
        if cleaned_sender.startswith('62'):
            cleaned_sender = cleaned_sender[2:]
            
        staff_user = None
        for u in CustomUser.objects.filter(is_active=True, role='staff'):
            if u.no_hp:
                u_wa = u.no_hp.replace('+', '').replace(' ', '').replace('-', '').lstrip('0')
                if u_wa.startswith('62'):
                    u_wa = u_wa[2:]
                if u_wa == cleaned_sender:
                    staff_user = u
                    break

        if staff_user:
            from hr.models import DailyAttendanceSession, UnlockRequest
            today = timezone.localdate()
            sesi = DailyAttendanceSession.objects.filter(tanggal=today).first()
            if sesi:
                unlock_req = UnlockRequest.objects.filter(staff=staff_user, sesi=sesi).order_by('-waktu_request').first()
                if unlock_req:
                    # Update their reason with this incoming message text!
                    unlock_req.alasan = message_text
                    unlock_req.save()
                    
                    # Send a confirmation WhatsApp message back to the staff
                    confirm_msg = (
                        f"Terima kasih {staff_user.get_full_name() or staff_user.username}.\n\n"
                        f"Alasan Anda:\n"
                        f"*\"{message_text}\"*\n\n"
                        f"Telah berhasil dicatat dan diteruskan ke Manager untuk ditinjau. "
                        f"Anda akan menerima notifikasi jika akses Anda disetujui."
                    )
                    self._kirim_balas_async(sender_number, confirm_msg)
                    
                    # Also notify the Manager / Owner!
                    try:
                        manager_user = sesi.dihidupkan_oleh or CustomUser.objects.filter(role__in=['manager', 'owner'], is_active=True).first()
                        if manager_user and manager_user.no_hp:
                            mgr_wa = manager_user.no_hp.replace('+', '').replace(' ', '').replace('-', '')
                            mgr_msg = (
                                f"🚨 *PEMBERITAHUAN ABSENSI STAFF* 🚨\n\n"
                                f"Staff *{staff_user.get_full_name() or staff_user.username}* memberikan alasan absensi masuk hari ini:\n"
                                f"💬 *\"{message_text}\"*\n\n"
                                f"Silakan periksa halaman dashboard HR CRM untuk menyetujui (Approve) atau menolak (Reject) permintaan buka kunci."
                            )
                            self._kirim_balas_async(mgr_wa, mgr_msg)
                    except Exception as e:
                        logger.error(f"Gagal mengirim notifikasi alasan staff ke manager: {e}")
                        
                    return Response({'status': 'staff_attendance_reason_captured'}, status=status.HTTP_200_OK)

        # Ambil kontak
        contact_obj = Contact.objects.filter(nomor_wa=sender_number).first()
        nama_pelanggan = contact_obj.nama if contact_obj else ""
        p_kecil = message_text.lower()
        panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"

        # Cek status Human Handover (aktif 15 menit jika staff membalas chat, atau jika diset manual di DB)
        if cache.get(f"wa_handover_{sender_number}") or (contact_obj and getattr(contact_obj, 'handover_to_staff', False)):
            logger.info(f"Chat dengan {sender_number} sedang dalam mode Human Handover. Bot diabaikan.")
            return Response({'status': 'handover_mode_active'}, status=status.HTTP_200_OK)

        # Cek jika ada custom welcome / override response di SystemConfig
        try:
            custom_response = SystemConfig.objects.get(key='custom_bot_response').value
            if custom_response and custom_response.strip():
                # Kirim sambutan HANYA jika kontak baru / belum punya nama kustom (tidak untuk kontak yang sudah ada namanya di DB)
                is_new_contact = (
                    not contact_obj or 
                    not contact_obj.nama or 
                    contact_obj.nama.strip() == "" or 
                    contact_obj.nama == "Pelanggan"
                )
                if is_new_contact:
                    greeted_cache_key = f"wa_greeted_{sender_number}"
                    if not cache.get(greeted_cache_key):
                        push_name = event_data.get('pushName', '') or "Pelanggan"
                        if not contact_obj:
                            contact_obj, _ = Contact.objects.get_or_create(
                                    nomor_wa=sender_number,
                                    defaults={'nama': push_name}
                                )
                        elif not contact_obj.nama or contact_obj.nama == "Pelanggan":
                            if push_name:
                                contact_obj.nama = push_name
                                contact_obj.save()
                        
                        # Ambil nama ter-update untuk memori
                        nama_pelanggan = contact_obj.nama
                        panggilan = f"Kak {nama_pelanggan}" if nama_pelanggan else "Kak"
                        
                        cache.set(greeted_cache_key, True, timeout=86400) # Greeted selama 24 jam
                        self._kirim_balas_async(sender_number, custom_response.strip())
                        return Response({'status': 'custom_welcome_sent'}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.warning(f"Gagal memproses custom_bot_response: {e}")

        jawaban = ""

        # Step 1: Tanya nama jika kontak baru
        if not nama_pelanggan and sender_number not in menunggu_nama:
            menunggu_nama.add(sender_number)
            try:
                biz_name = SystemConfig.objects.get(key='bisnis_nama').value or 'Brandy'
            except Exception as e:
                logger.warning(f"Gagal mengambil nama bisnis: {e}")
                biz_name = 'Brandy'
            jawaban = (
                f"Halo Kak! 👋 Selamat datang di *{biz_name}*.\n\n"
                "Saya adalah *Asisten Virtual* Bintang Advertising yang siap membantu Kakak secara otomatis 24/7. 🤖\n\n"
                "Sebelum kita mulai, boleh tahu dengan Kakak siapa ini biar lebih enak ngobrolnya? 😊"
            )
            self._kirim_balas_async(sender_number, jawaban)
            return Response({'status': 'waiting_for_name_triggered'}, status=status.HTTP_200_OK)

        elif sender_number in menunggu_nama:
            nama_baru = ekstrak_nama_dari_pesan(message_text)
            contact_obj, _ = Contact.objects.get_or_create(
                nomor_wa=sender_number, defaults={'nama': nama_baru}
            )
            if not contact_obj.nama:
                contact_obj.nama = nama_baru
                contact_obj.save()
            elif contact_obj.nama != nama_baru:
                contact_obj.nama = nama_baru
                contact_obj.save()
            menunggu_nama.discard(sender_number)
            nama_pelanggan = nama_baru
            panggilan = f"Kak {nama_pelanggan}"
            jawaban = (
                f"Salam kenal {panggilan}! ✨\n"
                f"Ada yang bisa kami bantu hari ini? Mau cetak apa nih Kak?"
            )
            self._kirim_balas_async(sender_number, jawaban)
            return Response({'status': 'name_registered'}, status=status.HTTP_200_OK)

        # Simpan pesan masuk ke memori AI
        simpan_ke_memori(sender_number, "user", message_text, nama_pelanggan)

        # Step 2: Cek tracking / Kirim Desain pesanan
        is_form_order = (
            ('jenis produk' in p_kecil and ('no. wa' in p_kecil or 'item 1' in p_kecil or 'no wa' in p_kecil))
            or
            ('nama pemesan' in p_kecil and 'jenis produk' in p_kecil)
        )
        is_form_desain = 'tulisan yang dimuat' in p_kecil or 'dominan warna' in p_kecil

        jawaban = ""
        if not (is_form_order or is_form_desain):
            jawaban = proses_kirim_desain(message_text, sender_number, nama_pelanggan, media_url=media_url)
            if not jawaban:
                jawaban = cek_tracking(message_text, sender_number, nama_pelanggan)
            if jawaban:
                simpan_ke_memori(sender_number, "assistant", jawaban, nama_pelanggan)
                self._kirim_balas_async(sender_number, jawaban)
                return Response({'status': 'tracking_replied'}, status=status.HTTP_200_OK)

        # Step 3: Deteksi form order / desain
        is_form_order = (
            ('jenis produk' in p_kecil and ('no. wa' in p_kecil or 'item 1' in p_kecil or 'no wa' in p_kecil))
            or
            ('nama pemesan' in p_kecil and 'jenis produk' in p_kecil)
        )
        is_form_desain = 'tulisan yang dimuat' in p_kecil or 'dominan warna' in p_kecil

        if is_form_order or is_form_desain:
            # Bersihkan footer instruksi/konfirmasi
            detail_bersih = _re.split(r'(?i)===?\s*AKHIR\s*TEMPLATE\s*===?|⚠️\s*\*?PENTING:\*?|data\s+sudah\s+sesuai|desain\s+sudah\s+sesuai', message_text)[0].strip()
            try:
                if is_form_desain:
                    order_id, success, err_msg = update_desain_dari_form_data(detail_bersih)
                    if success:
                        jawaban = (
                            f"Terima kasih {panggilan}! Konsep desain sudah masuk ke Antrean Desain ✅\n\n"
                            f"🎫 *ID PESANAN: {order_id}*\n"
                            f"Tim desain kami akan segera memproses konsep Kakak. Mohon ditunggu ya! 🙏"
                        )
                    else:
                        jawaban = (
                            f"Maaf {panggilan}, gagal memproses konsep desain Kakak:\n"
                            f"⚠️ {err_msg}\n\n"
                            f"Mohon periksa kembali ID Pesanan Kakak dan kirimkan ulang dengan benar ya Kak. 🙏"
                        )
                else:
                    order_id, is_desain_ready = self._simpan_order_dari_form(sender_number, nama_pelanggan, detail_bersih)
                    order_instance = Order.objects.prefetch_related('items').get(id=order_id)
                    label = "Pesanan Anda telah masuk ke sistem kami"
                    
                    item_lines = []
                    total_estimasi = 0
                    for item in order_instance.items.all():
                        spec_parts = []
                        if item.panjang > 0 and item.lebar > 0:
                            spec_parts.append(f"{item.panjang:.1f}x{item.lebar:.1f}m")
                        if item.bahan:
                            spec_parts.append(item.bahan)
                        spec_str = f" ({', '.join(spec_parts)})" if spec_parts else ""
                        
                        price_val = item.harga_jual or 0
                        price_display = f"Rp {price_val:,}" if price_val > 0 else "Hubungi Admin"
                        price_display = price_display.replace(',', '.')
                        item_lines.append(f"• *{item.jenis_produk}*{spec_str} - {item.qty}x")
                        item_lines.append(f"  └─ Est. Harga: *{price_display}*")
                        total_estimasi += price_val
                        
                    total_display = f"Rp {total_estimasi:,}" if total_estimasi > 0 else "Hubungi Admin"
                    total_display = total_display.replace(',', '.')
                    
                    jawaban = (
                        f"Terima kasih {panggilan}! {label} ✅\n\n"
                        f"🎫 *ID PESANAN: {order_id}*\n"
                        f"_Simpan ID ini untuk melacak status pesanan Kakak._\n\n"
                        f"📝 *RINCIAN PESANAN:*\n"
                        + "\n".join(item_lines) + "\n\n"
                        f"💰 *TOTAL ESTIMASI: {total_display}*\n\n"
                        f"Tim kami akan segera memverifikasi pesanan Kakak. Mohon ditunggu 🙏"
                    )
                    # Jika ada item yang belum memiliki desain, infokan link upload / petunjuk kirim langsung
                    from django.db.models import Q
                    has_no_design = order_instance.items.filter(Q(gdrive_customer_link__isnull=True) | Q(gdrive_customer_link='')).exists()
                    if has_no_design:
                        if is_desain_ready:
                            jawaban += (
                                f"\n\nSilakan kirimkan file desain Kakak langsung ke chat ini (sebagai Gambar atau Dokumen) "
                                f"dengan mencantumkan keterangan/caption ID Pesanan: *{order_id}* pada file tersebut ya Kak! 😊"
                            )
                        else:
                            jawaban += (
                                f"\n\nSilakan *copy-paste* dan isi **Form Konsep Desain** di bawah ini agar tim desainer kami bisa langsung memprosesnya:\n\n"
                                f"📋 *FORM KONSEP DESAIN*\n"
                                f"- ID Pesanan: {order_id}\n"
                                f"- Tulisan yang dimuat:\n"
                                f"- Dominan Warna:\n"
                                f"- Logo / Foto (Ada/Tidak):\n"
                                f"- Bentuk (Vertikal / Horizontal):\n"
                                f"- Request Tambahan:\n\n"
                                f"⚠️ *PENTING:* Setelah form diisi lengkap, tambahkan di baris paling bawah:\n"
                                f"*DESAIN SUDAH SESUAI*\n"
                                f"agar konsep desain otomatis masuk ke sistem kami. 👇"
                            )
            except ValueError as ve:
                jawaban = (
                    f"Maaf {panggilan}, format pengisian form pesanan Kakak belum lengkap / ada yang salah:\n\n"
                    f"⚠️ {str(ve)}\n\n"
                    f"Mohon perbaiki dan kirimkan ulang dengan format yang benar ya Kak. 🙏😊"
                )

        # Step 4: Cek tanya harga
        if not jawaban:
            jawaban = cek_harga(message_text, nama_pelanggan)

        # Step 5: Cek rules awal
        if not jawaban:
            jawaban = cek_rules_awal(message_text, sender_number, nama_pelanggan)

        # Step 6: FAQ dari database
        if not jawaban:
            jawaban = cek_database_faq(message_text, nama_pelanggan)

        # Step 7: AI Fallback
        if not jawaban:
            jawaban = tanya_ai_finishing(sender_number)

        simpan_ke_memori(sender_number, "assistant", jawaban, nama_pelanggan)
        self._kirim_balas_async(sender_number, jawaban)

        return Response({'status': 'processed'}, status=status.HTTP_200_OK)

    def _kirim_balas_async(self, number, text):
        """
        Kirim balasan dengan simulasi mengetik secara asinkron di thread terpisah.
        Tracks task status in Django cache.
        """
        import threading
        import time
        import uuid
        from django.core.cache import cache

        task_id = f"async_task_{uuid.uuid4().hex}"
        cache.set(task_id, {"status": "pending", "number": number, "text": text[:50], "timestamp": time.time()}, timeout=3600)

        def worker():
            cache.set(task_id, {"status": "running", "number": number, "text": text[:50], "timestamp": time.time()}, timeout=3600)
            
            # Hitung delay berdasarkan panjang karakter (misal 30 karakter per detik)
            char_delay = len(text) / 30.0
            total_delay = min(max(2.0, char_delay), 15.0)

            # Tampilkan status sedang mengetik
            try:
                whatsapp_client.send_presence(number, "composing")
            except Exception as e:
                logger.warning(f"Failed to send presence composing: {e}")
                
            time.sleep(total_delay)
            
            # Kirim pesan dan matikan presence dengan retry
            success = False
            error_msg = ""
            for attempt in range(3):
                try:
                    res = whatsapp_client.send_text_message(number, text)
                    if res:
                        success = True
                        break
                    else:
                        error_msg = "Evolution API returned empty response"
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Attempt {attempt+1} failed to send WA message: {e}")
                time.sleep(2)
            
            try:
                whatsapp_client.send_presence(number, "paused")
            except Exception as e:
                logger.warning(f"Failed to send presence paused: {e}")

            if success:
                cache.set(task_id, {"status": "success", "number": number, "timestamp": time.time()}, timeout=3600)
            else:
                logger.critical(f"[WA_SEND_FAILURE] Failed to send message to {number} after 3 attempts. Error: {error_msg}. Text: {text}")
                cache.set(task_id, {"status": "failed", "number": number, "error": error_msg, "timestamp": time.time()}, timeout=86400)

        threading.Thread(target=worker, daemon=True).start()
        return task_id

    def _simpan_order_dari_form(self, nomor, nama_kontak, detail):
        def ambil_field(teks, *keys, max_len=1000):
            keys_all = [
                'Nama Pemesan', 'Nama', 'No. WA', 'No WA',
                'Jenis Produk', 'Jumlah', 'Ukuran',
                'Bahan/Material', 'Bahan / Material', 'Bahan',
                'Finishing', 'File Desain', 'Keterangan',
                'data sudah sesuai', 'desain sudah sesuai'
            ]
            escaped_keys = []
            for k in keys_all:
                pat = _re.escape(k).replace('\\ ', ' ').replace(' ', '[ \t\xa0]+')
                escaped_keys.append(pat)
            
            escaped_keys.append(r'===?\s*AKHIR\s*TEMPLATE\s*===?')
            escaped_keys.append(r'⚠️\s*\*?PENTING:\*?')
            lookahead_keys_pat = "|".join(escaped_keys)

            for key in keys:
                key_pat = _re.escape(key).replace('\\ ', ' ').replace(' ', '[ \t\xa0]+')
                pattern = rf'(?:[-*••]|\d+\.)?[ \t\xa0]*{key_pat}[ \t\xa0]*[:=][ \t\xa0]*(.*?)(?=\r?\n[ \t\xa0]*(?:[-*••]|\d+\.)?[ \t\xa0]*(?:{lookahead_keys_pat})[ \t\xa0]*[:=]|\r?\n[ \t\xa0]*(?:{lookahead_keys_pat})|$)'
                match = _re.search(pattern, teks, _re.IGNORECASE | _re.DOTALL)
                if match:
                    val = match.group(1).strip().strip('*_')
                    if val and val not in ('-', 'sudah ada / belum ada', '*sudah ada* / *belum ada*'):
                        val = _re.sub(r'<[^>]*>', '', val)
                        return val[:max_len].strip()
            return ''

        nama_dari_form = (
            ambil_field(detail, 'Nama Pemesan', 'Nama') or nama_kontak or '-'
        )

        blok_items = _re.split(r'(?im)^[ \t]*[-*•\[\*_]*\s*(?:📦\s*)?[\*_]*item\s+\d+[\*_\]:]*[ \t]*[\*_]*[^\r\n]*$', detail)
        blok_items = [b.strip() for b in blok_items if b.strip()]

        if len(blok_items) <= 1:
            blok_items = [detail]

        nama_order = nama_dari_form

        with transaction.atomic():
            contact, _ = Contact.objects.get_or_create(
                nomor_wa=nomor, defaults={'nama': nama_kontak}
            )
            existing_orders = Order.objects.filter(nomor_wa=nomor)
            contact.total_order = existing_orders.count() + 1
            contact.total_spent = sum(
                item.harga_jual
                for o in existing_orders.prefetch_related('items')
                for item in o.items.all()
            )
            contact.last_order  = timezone.localdate()
            contact.save()

            order_id = f"ORD-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
            order = Order.objects.create(
                id=order_id,
                nomor_wa=contact.nomor_wa,
                nama=nama_order,
                status_global='draft',
                catatan_pelanggan=detail,  # Store the full raw form message
            )

            items_dibuat = 0
            is_desain_ready = False
            for blok in blok_items:
                if not blok.strip():
                    continue

                jenis_produk = ambil_field(blok, 'Jenis Produk', max_len=100) or 'Umum'
                jumlah_str   = ambil_field(blok, 'Jumlah', max_len=50)
                ukuran       = ambil_field(blok, 'Ukuran', max_len=100)
                bahan        = ambil_field(blok, 'Bahan/Material', 'Bahan / Material', 'Bahan', max_len=100)
                finishing    = ambil_field(blok, 'Finishing', max_len=250)
                file_desain  = ambil_field(blok, 'File Desain', max_len=250).lower()
                if file_desain and 'belum' not in file_desain:
                    is_desain_ready = True
                keterangan   = ambil_field(blok, 'Keterangan', max_len=1000)

                if jenis_produk == 'Umum' and not ukuran and not bahan:
                    continue

                if not jumlah_str or not jumlah_str.strip():
                    raise ValueError(f"Kolom 'Jumlah' pada Item {items_dibuat+1} tidak boleh kosong.")
                
                # Extract the first consecutive block of digits instead of filtering out all non-digits (which turns "2.5" into "25")
                match_qty = _re.search(r'\d+', jumlah_str)
                digits_only = match_qty.group(0) if match_qty else ''
                
                if not digits_only:
                    raise ValueError(f"Jumlah '{jumlah_str}' pada Item {items_dibuat+1} tidak mengandung angka yang valid.")
                
                try:
                    qty = int(digits_only)
                    if qty <= 0:
                        raise ValueError(f"Jumlah '{qty}' pada Item {items_dibuat+1} harus lebih dari nol.")
                except ValueError:
                    raise ValueError(f"Jumlah '{jumlah_str}' pada Item {items_dibuat+1} bukan angka yang valid.")

                # Parse panjang & lebar
                panjang = 0.0
                lebar = 0.0
                if ukuran:
                    dimensi_match = _re.search(r'([\d.,]+)\s*[xX*]\s*([\d.,]+)', ukuran)
                    if dimensi_match:
                        try:
                            panjang = float(dimensi_match.group(1).replace(',', '.'))
                            lebar = float(dimensi_match.group(2).replace(',', '.'))
                        except ValueError:
                            pass

                detail_json = []
                if ukuran: detail_json.append({"key": "Ukuran", "value": ukuran})
                if finishing: detail_json.append({"key": "Finishing", "value": finishing})
                if bahan: detail_json.append({"key": "Bahan", "value": bahan})

                gdrive_link = ''
                link_match = _re.search(r'(https?://\S+)', blok)
                if link_match:
                    gdrive_link = link_match.group(1)

                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk=jenis_produk,
                    qty=qty,
                    panjang=panjang,
                    lebar=lebar,
                    bahan=bahan or '',
                    harga_jual=0,
                    detail=detail_json,
                    keterangan_detail=keterangan or '',
                    gdrive_customer_link=gdrive_link,
                )

                # Tentukan tahap awal
                if 'belum' in file_desain:
                    tahap_awal = TahapProses.objects.filter(
                        nama__icontains='desain'
                    ).order_by('urutan').first()
                else:
                    tahap_awal = TahapProses.objects.order_by('urutan').first()

                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )
                items_dibuat += 1

            if items_dibuat == 0:
                order_item = OrderItem.objects.create(
                    order=order,
                    jenis_produk='Umum',
                    qty=1,
                    harga_jual=0,
                    detail=[{"key": "Info", "value": "Format tidak terurai"}],
                    keterangan_detail=detail[:200],
                )
                tahap_awal = TahapProses.objects.order_by('urutan').first()
                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=order_item,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean'
                    )

        return order_id, is_desain_ready


class WhatsAppStatusView(APIView):
    """
    GET /api/whatsapp/status/
    Returns the current connection state of the WhatsApp instance
    and a QR code (base64) for scanning if not yet connected.
    Owner/Manager only.
    """
    permission_classes = [IsOwnerOrManager]

    def get(self, request):
        import requests as req_lib
        base_url = os.getenv("EVOLUTION_API_URL", "http://localhost:8080").rstrip('/')
        api_key  = os.getenv("EVOLUTION_API_KEY", "LocalTestingApiKey123")
        instance = os.getenv("EVOLUTION_INSTANCE_NAME", "bintang_instance")
        headers  = {"apikey": api_key}

        # 1. Get connection state
        state = "unknown"
        owner_jid = None
        try:
            r = req_lib.get(f"{base_url}/instance/connectionState/{instance}", headers=headers, timeout=5)
            if r.ok:
                data = r.json()
                state = data.get("instance", {}).get("state", "unknown")
        except Exception as e:
            logger.warning(f"Could not fetch WA connection state: {e}")

        # 2. If not connected, get QR code
        qr_base64 = None
        pairing_code = None
        if state in ("connecting", "close", "unknown"):
            try:
                r = req_lib.get(f"{base_url}/instance/connect/{instance}", headers=headers, timeout=10)
                if r.ok:
                    data = r.json()
                    qr_base64 = data.get("base64")
                    pairing_code = data.get("pairingCode")
            except Exception as e:
                logger.warning(f"Could not fetch WA QR code: {e}")

        # 3. Get instance info (message/chat count etc)
        instance_info = {}
        try:
            r = req_lib.get(f"{base_url}/instance/fetchInstances", headers=headers, timeout=5)
            if r.ok:
                instances = r.json()
                for inst in (instances if isinstance(instances, list) else []):
                    if inst.get("name") == instance:
                        instance_info = {
                            "ownerJid":    inst.get("ownerJid"),
                            "profileName": inst.get("profileName"),
                            "messageCount": inst.get("_count", {}).get("Message", 0),
                            "chatCount":    inst.get("_count", {}).get("Chat", 0),
                        }
                        owner_jid = inst.get("ownerJid")
                        break
        except Exception as e:
            logger.warning(f"Could not fetch WA instance info: {e}")

        return Response({
            "state":        state,
            "connected":    state == "open",
            "owner_jid":    owner_jid,
            "qr_base64":    qr_base64,
            "pairing_code": pairing_code,
            "instance_name": instance,
            **instance_info,
        })


class WhatsAppChatsView(APIView):
    """
    GET /api/whatsapp/chats/
    Retrieves all active chats from the WhatsApp Gateway (Evolution API).
    """
    permission_classes = [IsOwnerManagerAdminOrKasir]

    def get(self, request):
        chats = whatsapp_client.get_chats()
        return Response(chats)


class WhatsAppMessagesView(APIView):
    """
    GET /api/whatsapp/messages/?number=628xx
    Retrieves message history for a specific number from Evolution API.
    """
    permission_classes = [IsOwnerManagerAdminOrKasir]

    def get(self, request):
        number = request.query_params.get('number')
        if not number:
            return Response({"error": "Query parameter 'number' is required"}, status=400)
        
        limit = int(request.query_params.get('limit', 50))
        messages = whatsapp_client.get_messages(number, limit=limit)
        return Response(messages)


class WhatsAppSendMessageView(APIView):
    """
    POST /api/whatsapp/send/
    Sends a WhatsApp message manually to a contact.
    """
    permission_classes = [IsOwnerManagerAdminOrKasir]

    def post(self, request):
        number = request.data.get('number')
        text = request.data.get('text')
        if not number or not text:
            return Response({"error": "Fields 'number' and 'text' are required"}, status=400)
        
        result = whatsapp_client.send_text_message(number, text)
        if result:
            try:
                clean_num = number.replace('+', '').replace(' ', '').replace('-', '').split('@')[0]
                cache.set(f"wa_handover_{clean_num}", True, timeout=900)
                logger.info(f"Staff manually sent message to {clean_num}. Handover enabled for 15 mins.")
            except Exception as e:
                logger.error(f"Failed to set handover cache on manual send: {e}")
            return Response(result)
        return Response({"error": "Failed to send message via WhatsApp Gateway"}, status=500)


class WhatsAppSendMediaView(APIView):
    """
    POST /api/whatsapp/send-media/
    Uploads a file to Cloudflare R2 / Django storage, then sends it via WhatsApp.
    Accepts:
      - file: multipart file
      - number: string
      - caption: string (optional)
    """
    permission_classes = [IsOwnerManagerAdminOrKasir]

    def post(self, request):
        import mimetypes
        from django.core.files.storage import default_storage
        from django.utils.text import get_valid_filename

        number = request.data.get('number')
        caption = request.data.get('caption', '')
        file_obj = request.FILES.get('file')

        if not number or not file_obj:
            return Response({"error": "Fields 'number' and 'file' are required"}, status=400)

        # 1. Clean file name
        cleaned_filename = get_valid_filename(file_obj.name)
        
        # Save to storage (R2 in production, local in dev)
        unique_name = f"whatsapp_media/{uuid.uuid4().hex}_{cleaned_filename}"
        
        try:
            saved_path = default_storage.save(unique_name, file_obj)
            relative_url = default_storage.url(saved_path)
            
            # Make sure we have an absolute URL
            if relative_url.startswith('/'):
                media_url = request.build_absolute_uri(relative_url)
            else:
                media_url = relative_url

            # 2. Detect mime type & media type
            mime_type, _ = mimetypes.guess_type(cleaned_filename)
            if not mime_type:
                mime_type = "application/octet-stream"

            media_type = "document"
            if mime_type.startswith("image/"):
                media_type = "image"
            elif mime_type.startswith("video/"):
                media_type = "video"
            elif mime_type.startswith("audio/"):
                media_type = "audio"

            # 3. Send via Evolution API
            result = whatsapp_client.send_media_message(
                number=number,
                media_url=media_url,
                media_type=media_type,
                mime_type=mime_type,
                file_name=cleaned_filename,
                caption=caption
            )

            if result:
                try:
                    clean_num = number.replace('+', '').replace(' ', '').replace('-', '').split('@')[0]
                    cache.set(f"wa_handover_{clean_num}", True, timeout=900)
                    logger.info(f"Staff manually sent media to {clean_num}. Handover enabled for 15 mins.")
                except Exception as e:
                    logger.error(f"Failed to set handover cache on manual media send: {e}")
                return Response({
                    "status": "success",
                    "media_url": media_url,
                    "result": result
                })
            
            return Response({"error": "Failed to send media via WhatsApp Gateway"}, status=500)

        except Exception as e:
            logger.error(f"Error handling WhatsApp send media upload: {e}", exc_info=True)
            return Response({"error": str(e)}, status=500)
