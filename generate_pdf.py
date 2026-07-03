import os
import sys

# Ensure reportlab is available
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
except ImportError:
    print("Error: Library 'reportlab' belum terinstall.")
    print("Silakan install terlebih dahulu dengan menjalankan perintah: uv pip install reportlab  atau  pip install reportlab")
    sys.exit(1)

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        if self._pageNumber == 1:
            return  # Skip cover page
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header
        self.drawString(54, 750, "SPESIFIKASI TEKNIS & ARSITEKTUR SYSTEM: BINTANG CRM & ERP")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 742, 558, 742)
        
        # Footer
        self.line(54, 55, 558, 55)
        page_text = f"Halaman {self._pageNumber} dari {page_count}"
        self.drawRightString(558, 40, page_text)
        self.drawString(54, 40, "Confidential - Bintang Advertising CRM & ERP")
        self.restoreState()

def build_pdf(filename):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=72,
        bottomMargin=72
    )

    styles = getSampleStyleSheet()
    
    primary_color = colors.HexColor("#1E293B")
    secondary_color = colors.HexColor("#0D9488")
    dark_gray = colors.HexColor("#334155")
    light_gray = colors.HexColor("#F8FAFC")
    border_color = colors.HexColor("#E2E8F0")

    title_style = ParagraphStyle(
        'CoverTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=30,
        textColor=primary_color,
        spaceAfter=10
    )

    subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=secondary_color,
        spaceAfter=40
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=primary_color,
        spaceBefore=15,
        spaceAfter=10,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=secondary_color,
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=dark_gray,
        spaceAfter=8
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=dark_gray,
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=4
    )

    story = []

    # ================= PAGE 1: COVER =================
    story.append(Spacer(1, 100))
    story.append(Paragraph("SPESIFIKASI TEKNIS & CETAK BIRU SISTEM", subtitle_style))
    story.append(Paragraph("BINTANG ADVERTISING<br/>CRM & ERP PLATFORM", title_style))
    story.append(Paragraph("Dokumen Spesifikasi Fungsional, Skalabilitas Akun Karyawan, dan Panduan Integrasi Sistem Perusahaan", subtitle_style))
    story.append(Spacer(1, 150))
    
    meta_text = (
        "<b>Dibuat Untuk:</b> Management Bintang Advertising<br/>"
        "<b>Versi Dokumen:</b> 1.0 (Production Release)<br/>"
        "<b>Tanggal:</b> 5 Juni 2026<br/>"
        "<b>Teknologi:</b> Django REST (ASGI Cluster) & React.js & Evolution API v2<br/>"
        "<b>Klasifikasi:</b> Rahasia Internal (Confidential)"
    )
    story.append(Paragraph(meta_text, body_style))
    story.append(PageBreak())

    # ================= PAGE 2: RINGKASAN & ARSITEKTUR =================
    story.append(Paragraph("1. Ringkasan Eksekutif & Tech Stack", h1_style))
    intro_p1 = (
        "Sistem Bintang Advertising CRM & ERP adalah solusi komprehensif terintegrasi yang dirancang untuk digitalisasi "
        "penuh operasional bisnis percetakan dan periklanan. Platform ini mengintegrasikan seluruh lini operasional "
        "mulai dari pencatatan prospek, pelacakan proses produksi (Kanban Board), otomatisasi inventaris barang berbasis "
        "resep (BoM), absensi kepegawaian dengan verifikasi WhatsApp, pengelolaan buku besar keuangan, hingga layanan "
        "Live Chat multi-agent WhatsApp."
    )
    story.append(Paragraph(intro_p1, body_style))
    
    story.append(Paragraph("Komponen Inti Infrastruktur:", h2_style))
    story.append(Paragraph("• <b>Backend Engine</b>: Django REST Framework berjalan di atas klaster server ASGI <b>Daphne</b> (4 worker) di balik reverse proxy <b>Nginx</b>.", bullet_style))
    story.append(Paragraph("• <b>Database & Caching</b>: MySQL Production Engine untuk konsistensi data, ditambah <b>Redis Cache</b> untuk akselerasi performa data statis.", bullet_style))
    story.append(Paragraph("• <b>Media Server</b>: Cloudflare R2 Object Storage (S3-Compatible) untuk mengarsip dokumen nota, slip gaji, dan file desain grafis.", bullet_style))
    story.append(Paragraph("• <b>WhatsApp Engine</b>: Evolution API v2 Dockerized terhubung ke database PostgreSQL khusus untuk penanganan webhook.", bullet_style))
    story.append(Paragraph("• <b>Frontend Client</b>: React.js SPA terkompilasi menggunakan Vite & Rolldown.", bullet_style))
    story.append(Spacer(1, 15))

    # ================= PAGE 3: PENJABARAN FITUR LENGKAP =================
    story.append(Paragraph("2. Penjabaran Fitur Lengkap Modul", h1_style))
    
    story.append(Paragraph("Modul CRM & Kontak Pelanggan", h2_style))
    story.append(Paragraph("Menyimpan seluruh data transaksi pelanggan secara terpusat. Dilengkapi fitur pencarian pintar berbasis server database dan kalkulasi piutang otomatis (remaining balance) per pelanggan secara real-time.", body_style))
    
    story.append(Paragraph("Modul Pemesanan (Order Management)", h2_style))
    story.append(Paragraph("Pembuatan nota pesanan dengan sistem penomoran otomatis terstruktur, detail multi-item produk, kalkulasi uang muka (DP), penentuan diskon, log aktivitas audit perubahan status, dan pengunggahan file desain langsung ke Cloudflare R2 Cloud Storage.", body_style))

    story.append(Paragraph("Modul Papan Produksi (Job Board)", h2_style))
    story.append(Paragraph("Alur kerja produksi visual berbasis papan Kanban (Desain, Cetak, Finishing, QC, Siap Kirim, Selesai). Tugas didelegasikan ke staf produksi secara langsung, yang dapat diakses staf melalui dasbor khusus mereka.", body_style))

    story.append(Paragraph("Modul Inventaris & Bill of Materials (BOM)", h2_style))
    story.append(Paragraph("Pencatatan persediaan bahan baku terintegrasi dengan modul pemesanan. Saat status pesanan diubah menjadi 'Selesai', sistem secara otomatis memotong stok bahan baku di gudang sesuai dengan resep BoM produk tersebut.", body_style))

    story.append(Paragraph("Modul Absensi & HR (Kepegawaian)", h2_style))
    story.append(Paragraph("Profil karyawan, generate otomatis NIP aman, kontrak kerja PKWT digital, absensi harian berbasis shift, serta fitur <i>Unlock Request</i> otomatis bagi karyawan terlambat via bot WhatsApp.", body_style))

    story.append(Paragraph("Modul Buku Besar & Keuangan (General Ledger)", h2_style))
    story.append(Paragraph("Manajemen bagan akun keuangan (Chart of Accounts), pencatatan jurnal umum double-entry untuk transaksi operasional, penggajian (payroll) bulanan, lembur, bonus, dan pencetakan slip gaji.", body_style))

    story.append(Paragraph("Modul WhatsApp Chatbot & Live Chat", h2_style))
    story.append(Paragraph("Antarmuka Live Chat mirip WhatsApp Web untuk melayani pelanggan langsung dari aplikasi. Dilengkapi bot otomatisasi penerima form pesanan/desain, tracker status pesanan mandiri, dan integrasi persetujuan absensi HR.", body_style))

    story.append(PageBreak())

    # ================= PAGE 4: KALKULASI KAPASITAS & SKALABILITAS =================
    story.append(Paragraph("3. Analisis & Kalkulasi Skalabilitas Akun Karyawan", h1_style))
    story.append(Paragraph(
        "Sistem ini dirancang dengan prinsip efisiensi query dan asinkronus, sehingga memiliki skalabilitas yang "
        "luar biasa untuk menampung jumlah akun karyawan, staf, dan pengguna lainnya.", body_style
    ))
    
    story.append(Paragraph("Kategori Skalabilitas Infrastruktur:", h2_style))
    
    table_data = [
        [
            Paragraph("<b>Jumlah Pengguna</b>", body_style),
            Paragraph("<b>Kebutuhan VPS Server</b>", body_style),
            Paragraph("<b>Dampak Performa & Penanganan Sistem</b>", body_style)
        ],
        [
            Paragraph("<b>1 - 100 Staf</b><br/>(Skala UKM / Menengah)", body_style),
            Paragraph("• CPU: 2 Core<br/>• RAM: 2 GB<br/>• Storage: 20 GB SSD", body_style),
            Paragraph("Sistem berjalan sangat ringan. Daphne ASGI cluster dapat memproses seluruh API call dan WebSocket secara instan. Database MySQL lokal tidak mengalami antrean lock transaksi.", body_style)
        ],
        [
            Paragraph("<b>100 - 1.000 Staf</b><br/>(Skala Korporat / Multi-Cabang)", body_style),
            Paragraph("• CPU: 4 Core<br/>• RAM: 8 GB<br/>• Storage: 50 GB SSD", body_style),
            Paragraph("Daphne ASGI worker dinaikkan menjadi 8 instance. Koneksi Redis digunakan untuk session sharing. Database MySQL diindeks secara penuh pada relasi kunci asing (Foreign Key).", body_style)
        ],
        [
            Paragraph("<b>1.000 - 10.000+ Staf</b><br/>(Skala Enterprise / Nasional)", body_style),
            Paragraph("• CPU: Multi-Server<br/>• RAM: Terdistribusi<br/>• Database Terpisah", body_style),
            Paragraph("Horizontal scaling diterapkan. Server database MySQL dipisah dari server backend (Daphne). Cloudflare R2 tetap mengelola media storage secara independen tanpa membebani server core.", body_style)
        ]
    ]

    t = Table(table_data, colWidths=[120, 150, 230])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, border_color),
        ('BACKGROUND', (0, 1), (-1, 1), colors.white),
        ('BACKGROUND', (0, 2), (-1, 2), colors.white),
        ('BACKGROUND', (0, 3), (-1, 3), colors.white),
    ]))
    story.append(t)
    story.append(Spacer(1, 15))

    story.append(Paragraph("Faktor Kunci Penentu Ketangguhan:", h2_style))
    story.append(Paragraph("• <b>Optimasi Server-Side Pagination</b>: Data order dan kontak yang berjumlah puluhan ribu baris tidak dimuat sekaligus. Paginasi asinkronus (page size = 50) menjamin transmisi data JSON super cepat.", bullet_style))
    story.append(Paragraph("• <b>Database Locking (select_for_update)</b>: Mencegah konflik alokasi NIP karyawan dan pemotongan stok bahan baku secara paralel.", bullet_style))
    story.append(Paragraph("• <b>Outbound Anti-Looping Cache</b>: Mencegah kegagalan memori dan overhead server jika WhatsApp chatbot terjebak dalam loop pesan melingkar.", bullet_style))
    story.append(PageBreak())

    # ================= PAGE 5: FLEKSIBILITAS INTEGRASI =================
    story.append(Paragraph("4. Fleksibilitas & Sinkronisasi Sistem Perusahaan", h1_style))
    intro_flex = (
        "Sistem Bintang CRM & ERP dibangun di atas pondasi arsitektur <b>API-First</b>. Artinya, seluruh fungsionalitas "
        "sistem dapat diakses secara terprogram via antarmuka REST API yang aman, didokumentasikan menggunakan spesifikasi "
        "OpenAPI."
    )
    story.append(Paragraph(intro_flex, body_style))
    
    story.append(Paragraph("Metode Sinkronisasi dengan Sistem Eksternal (SAP, Odoo, Accurate, HRIS):", h2_style))
    story.append(Paragraph("1. <b>Sinkronisasi Real-Time berbasis Webhook</b>: Sistem dapat dikonfigurasi untuk memicu HTTP webhook keluar ketika ada aksi penting terjadi (misal: pesanan lunas, pendaftaran staf baru, atau stok barang di bawah limit).", bullet_style))
    story.append(Paragraph("2. <b>Autentikasi JWT yang Aman</b>: Integrasi eksternal dapat berkomunikasi secara aman melalui REST API dengan memanfaatkan Token Autentikasi JWT.", bullet_style))
    story.append(Paragraph("3. <b>Modul Independen Ekstensible</b>: Anda dapat menambahkan modul baru (misal: <code>api_integration_odoo</code>) untuk menangani pemetaan data internal ke skema Odoo tanpa merusak logika bisnis inti.", bullet_style))
    story.append(Paragraph("4. <b>Kompatibilitas S3-Storage</b>: File desain atau lampiran nota dapat diakses dan dibaca langsung oleh sistem luar perusahaan dengan izin akses URL presigned yang aman.", bullet_style))
    
    story.append(Spacer(1, 20))
    story.append(Paragraph("Kesimpulan Cetak Biru:", h2_style))
    kesimpulan_text = (
        "Sistem ini siap digunakan baik pada tingkat operasional UKM lokal (kapasitas puluhan staf) hingga tingkat "
        "skalabilitas korporasi dengan ribuan staf tanpa perlu merombak ulang kode program inti. Infrastruktur klaster "
        "ASGI, pemisahan cloud media storage, serta desain API-first memberikan jaminan keamanan investasi teknologi "
        "untuk jangka waktu yang sangat panjang."
    )
    story.append(Paragraph(kesimpulan_text, body_style))

    doc.build(story, canvasmaker=NumberedCanvas)

if __name__ == "__main__":
    output_filename = "bintang_crm_erp_specification.pdf"
    print(f"Memulai pembuatan PDF: {output_filename}...")
    build_pdf(output_filename)
    print(f"Selesai! PDF berhasil dibuat di direktori aktif Anda: {os.path.abspath(output_filename)}")
