import logging
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from .models import (
    Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard,
    InventoryItem, RestockHistory, ProductPrice, SystemConfig, FAQ,
    OrderActivityLog, KomplainOrder, KomplainLog, CustomerActivity,
    BillOfMaterials, BoMItem, ShiftTiming, POSAntrianDevice, SaldoKasHarian,
    RingkasanShift
)

logger = logging.getLogger(__name__)

# --- 1. Master Data Serializers ---
class ShiftTimingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftTiming
        fields = '__all__'

class DivisiSerializer(serializers.ModelSerializer):
    class Meta:
        model = Divisi
        fields = '__all__'

class TahapProsesSerializer(serializers.ModelSerializer):
    divisi_nama = serializers.ReadOnlyField(source='divisi.nama')

    class Meta:
        model = TahapProses
        fields = ['id', 'nama', 'divisi', 'divisi_nama', 'urutan']

# --- 2. Account & User Serializers ---
class CustomUserSerializer(serializers.ModelSerializer):
    divisi_nama = serializers.ReadOnlyField(source='divisi.nama')
    
    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'email', 'role', 'divisi', 'divisi_nama', 'no_hp', 'kota', 
            'negara', 'alamat', 'bio', 'foto_profil', 'last_login', 'date_joined', 
            'status_karyawan', 'jenis_kontrak', 'kontrak_mulai', 'kontrak_selesai',
            'no_kpj', 'bpjs_kes', 'file_pkwt', 'nip'
        ]

    def to_internal_value(self, data):
        # Buat salinan mutable jika data adalah QueryDict atau dict
        if hasattr(data, '_mutable'):
            data = data.copy()
        elif isinstance(data, dict):
            data = data.copy()

        # Bersihkan tanggal kosong menjadi None agar validasi serializer lolos
        for key in ['kontrak_mulai', 'kontrak_selesai']:
            if key in data and data[key] == '':
                data[key] = None
        return super().to_internal_value(data)

    def update(self, instance, validated_data):
        # Update base user model
        instance = super().update(instance, validated_data)

        # Sinkronisasi status_karyawan ke is_active
        if 'status_karyawan' in validated_data:
            if instance.status_karyawan == 'nonaktif':
                instance.is_active = False
            elif instance.status_karyawan in ['aktif', 'cuti']:
                instance.is_active = True
            instance.save()

        return instance

# Endpoint B-05: Serializer Khusus Pembuatan User dengan Hash Password
class CreateUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['username', 'password', 'role', 'divisi', 'no_hp', 'nip'] 
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def validate(self, attrs):
        # FIX: Validasi kekuatan password menggunakan Django password validators
        password = attrs.get('password')
        if password:
            # Buat instance user sementara untuk konteks validasi
            temp_user = CustomUser(username=attrs.get('username', ''))
            try:
                validate_password(password, user=temp_user)
            except DjangoValidationError as e:
                raise serializers.ValidationError({'password': list(e.messages)})
        return attrs

    def create(self, validated_data):
        user = CustomUser(
            username=validated_data['username'],
            role=validated_data.get('role', 'staff'),
            divisi=validated_data.get('divisi'),
            no_hp=validated_data.get('no_hp', ''),
            nip=validated_data.get('nip')
        )
        # Hash password sebelum disimpan
        user.set_password(validated_data['password'])
        user.save()
        return user

# --- 2.5 OrderItemShortSerializer (to avoid circular dependency) ---
class OrderItemShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = '__all__'

# --- 3. JobBoard Serializer ---
class JobBoardSerializer(serializers.ModelSerializer):
    tahap_nama        = serializers.ReadOnlyField(source='tahap.nama')
    tahap_divisi_nama = serializers.ReadOnlyField(source='tahap.divisi.nama')  # Untuk grouping per divisi
    pic_nama          = serializers.ReadOnlyField(source='pic_staff.username')
    pic_nip           = serializers.ReadOnlyField(source='pic_staff.nip')
    pic_divisi_nama   = serializers.ReadOnlyField(source='pic_staff.divisi.nama') # Fallback grouping
    order_item_detail = OrderItemShortSerializer(source='order_item', read_only=True)
    
    # Customer and order details for staff view
    pelanggan_nama    = serializers.ReadOnlyField(source='order_item.order.nama')
    pelanggan_wa      = serializers.ReadOnlyField(source='order_item.order.nomor_wa')
    order_id          = serializers.ReadOnlyField(source='order_item.order.id')
    nama_produk       = serializers.ReadOnlyField(source='order_item.jenis_produk')
    ukuran            = serializers.SerializerMethodField()

    class Meta:
        model = JobBoard
        fields = '__all__'

    def get_ukuran(self, obj):
        item = obj.order_item
        if item and item.panjang > 0 and item.lebar > 0:
            return f"{item.panjang} x {item.lebar} m"
        return "-"

# --- 4. Order Item Serializer (Detail Pecahan) ---
class OrderItemSerializer(serializers.ModelSerializer):
    jobs = JobBoardSerializer(many=True, read_only=True) # Nested JobBoard
    insentif = serializers.SerializerMethodField()
    biaya_desain = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = '__all__'
        read_only_fields = ['luas'] # Luas otomatis dihitung oleh backend, front-end dilarang kirim data ini

    def get_insentif(self, obj):
        job = obj.jobs.first()
        return job.insentif if job else 0

    def get_biaya_desain(self, obj):
        job = obj.jobs.first()
        return job.biaya_desain if job else 0

    def create(self, validated_data):
        current_user = validated_data.pop('_current_user', None)
        request = self.context.get('request')
        insentif_val = 0
        biaya_desain_val = 0
        if request and request.data:
            try: insentif_val = int(request.data.get('insentif', 0) or 0)
            except (ValueError, TypeError): pass
            try: biaya_desain_val = int(request.data.get('biaya_desain', 0) or 0)
            except (ValueError, TypeError): pass

        instance = OrderItem(**validated_data)
        if current_user:
            instance._current_user = current_user
        instance.save()

        # Create default job if needed
        from api.models import TahapProses, JobBoard
        tahap_awal = TahapProses.objects.order_by('urutan').first()
        if tahap_awal:
            JobBoard.objects.create(
                order_item=instance,
                tahap=tahap_awal,
                status_pekerjaan='antrean',
                insentif=insentif_val,
                biaya_desain=biaya_desain_val
            )
        return instance

    def update(self, instance, validated_data):
        current_user = validated_data.pop('_current_user', None)
        request = self.context.get('request')
        insentif_val = None
        biaya_desain_val = None
        if request and request.data:
            if 'insentif' in request.data:
                try: insentif_val = int(request.data.get('insentif', 0) or 0)
                except (ValueError, TypeError): pass
            if 'biaya_desain' in request.data:
                try: biaya_desain_val = int(request.data.get('biaya_desain', 0) or 0)
                except (ValueError, TypeError): pass

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if current_user:
            instance._current_user = current_user
        instance.save()

        if insentif_val is not None or biaya_desain_val is not None:
            jobs = instance.jobs.all()
            if jobs.exists():
                for job in jobs:
                    if insentif_val is not None:
                        job.insentif = insentif_val
                    if biaya_desain_val is not None:
                        job.biaya_desain = biaya_desain_val
                    job.save()
            else:
                from api.models import TahapProses, JobBoard
                tahap_awal = TahapProses.objects.order_by('urutan').first()
                if tahap_awal:
                    JobBoard.objects.create(
                        order_item=instance,
                        tahap=tahap_awal,
                        status_pekerjaan='antrean',
                        insentif=insentif_val or 0,
                        biaya_desain=biaya_desain_val or 0
                    )
        return instance

class OrderActivityLogSerializer(serializers.ModelSerializer):
    user_nama = serializers.ReadOnlyField(source='user.username')
    waktu_formatted = serializers.SerializerMethodField()

    class Meta:
        model = OrderActivityLog
        fields = ['id', 'user', 'user_nama', 'tindakan', 'keterangan', 'waktu', 'waktu_formatted']

    def get_waktu_formatted(self, obj):
        return obj.waktu.strftime('%Y-%m-%d %H:%M:%S')

# --- 5. Order Serializer (Induk Nota) ---
class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    activity_logs = OrderActivityLogSerializer(many=True, read_only=True)
    customer_total_spent = serializers.SerializerMethodField()
    customer_total_orders = serializers.SerializerMethodField()
    hpp_bahan = serializers.SerializerMethodField()
    margin_persen = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'waktu', 'nomor_wa', 'nama', 'status_global', 'catatan_pelanggan', 
            'items', 'activity_logs', 'dp_dibayar', 'diskon_persen', 'total_harga', 'sisa_tagihan', 'metode_pembayaran',
            'customer_total_spent', 'customer_total_orders',
            'hpp_bahan', 'margin_persen',
        ]
        extra_kwargs = {
            'id': {'read_only': True},       
            'waktu': {'required': False},    
            'total_harga': {'read_only': True},  # Biarkan backend yang menjumlahkan totalnya
            'sisa_tagihan': {'read_only': True}, # Backend auto kurang total dengan DP
        }

    def get_customer_total_spent(self, obj):
        try:
            contact = Contact.objects.get(nomor_wa=obj.nomor_wa)
            return contact.total_spent
        except Contact.DoesNotExist:
            return 0

    def get_customer_total_orders(self, obj):
        try:
            contact = Contact.objects.get(nomor_wa=obj.nomor_wa)
            return contact.total_order
        except Contact.DoesNotExist:
            return 0

    def get_hpp_bahan(self, obj):
        """Hitung HPP bahan baku dari RestockHistory yang terkait job order ini."""
        try:
            total_hpp = 0.0
            jobs = JobBoard.objects.filter(order_item__order=obj).prefetch_related(
                'order_item__order'
            )
            for job in jobs:
                histories = RestockHistory.objects.filter(
                    keterangan__icontains=f'Job #{job.id}',
                    delta__lt=0  # hanya pemakaian (delta negatif)
                ).select_related('item')
                for h in histories:
                    total_hpp += abs(h.delta) * (h.item.cost_per_unit or 0)
            return round(total_hpp)
        except Exception as e:
            logger.exception("Gagal menghitung HPP bahan baku")
            return 0

    def get_margin_persen(self, obj):
        """Persentase margin keuntungan: (Omset - HPP) / Omset * 100"""
        try:
            hpp = self.get_hpp_bahan(obj)
            omset = obj.total_harga or 0
            if omset <= 0:
                return None
            margin = round(((omset - hpp) / omset) * 100, 1)
            return margin
        except Exception as e:
            logger.exception("Gagal menghitung margin persen")
            return None

    def create(self, validated_data):
        current_user = validated_data.pop('_current_user', None)
        instance = Order(**validated_data)
        if current_user:
            instance._current_user = current_user
        instance.save()
        return instance

# --- 6. Contact & Pendukung ---
class ContactSerializer(serializers.ModelSerializer):
    total_piutang = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = ['nomor_wa', 'nama', 'total_order', 'total_spent', 'last_order', 'keterangan', 'total_piutang', 'handover_to_staff']

    def get_total_piutang(self, obj):
        if hasattr(obj, 'annotated_piutang'):
            return obj.annotated_piutang or 0
        from django.db.models import Sum
        result = Order.objects.filter(
            nomor_wa=obj.nomor_wa
        ).exclude(status_global='batal').aggregate(total=Sum('sisa_tagihan'))['total']
        return result or 0

# --- 7. Inventory Serializer ---
class RestockHistorySerializer(serializers.ModelSerializer):
    user_nama = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = RestockHistory
        fields = ['id', 'delta', 'stok_awal', 'stok_akhir', 'keterangan', 'waktu', 'user_nama']

class InventoryItemSerializer(serializers.ModelSerializer):
    # id auto-generate di backend, tidak perlu dikirim client
    id         = serializers.CharField(read_only=True)
    is_kritis  = serializers.SerializerMethodField()
    nilai_stok = serializers.SerializerMethodField()
    history    = RestockHistorySerializer(many=True, read_only=True)

    def get_is_kritis(self, obj):
        return obj.stok < obj.min_stok

    def get_nilai_stok(self, obj):
        return round(obj.stok * obj.cost_per_unit, 2)

    class Meta:
        model  = InventoryItem
        fields = '__all__'


class ProductPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductPrice
        fields = '__all__'

# FIX: Rename dari AppConfigSerializer -> SystemConfigSerializer sesuai perubahan nama model
class SystemConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemConfig
        fields = '__all__'

# --- 9. Business Settings Serializer ---
class BusinessSettingsSerializer(serializers.Serializer):
    """
    Baca/tulis pengaturan bisnis dari SystemConfig (key-value store).
    Mirip OrgSettingsSerializer di Django CRM, tapi berbasis SystemConfig.
    Divisi digunakan sebagai 'org' — daftar divisi tersedia sebagai metadata.
    """
    nama_bisnis     = serializers.CharField(max_length=200, required=False, allow_blank=True)
    alamat          = serializers.CharField(required=False, allow_blank=True)
    no_telepon      = serializers.CharField(max_length=30, required=False, allow_blank=True)
    mata_uang       = serializers.CharField(max_length=10, required=False, allow_blank=True, default='IDR')
    # BUG FIX: URLField dengan allow_blank=True kadang masih melempar ValidationError
    # untuk string kosong pada beberapa versi DRF. Pakai CharField agar aman.
    logo_url        = serializers.CharField(required=False, allow_blank=True)
    logo_file       = serializers.ImageField(required=False, write_only=True)
    deskripsi       = serializers.CharField(required=False, allow_blank=True)
    
    # Keuangan & Invoice
    ppn_default     = serializers.IntegerField(required=False, default=11)
    invoice_terms   = serializers.CharField(required=False, allow_blank=True, default='')
    order_prefix    = serializers.CharField(max_length=20, required=False, allow_blank=True, default='ORD')
    
    # Kebijakan Karyawan & Payroll
    payroll_jam_masuk = serializers.CharField(max_length=10, required=False, allow_blank=True, default='08:00')
    payroll_jam_keluar = serializers.CharField(max_length=10, required=False, allow_blank=True, default='17:00')
    payroll_toleransi_menit = serializers.IntegerField(required=False, default=15)
    biaya_potongan_terlambat = serializers.IntegerField(required=False, default=1000)
    
    # API & Integrasi Cloud (R2 & SMTP)
    r2_bucket_name  = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    r2_custom_domain = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    smtp_host       = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    smtp_port       = serializers.IntegerField(required=False, default=587)
    smtp_user       = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    smtp_password   = serializers.CharField(max_length=100, required=False, allow_blank=True, default='', style={'input_type': 'password'})

    # Catatan Resi POS Settings
    pos_resi_judul = serializers.CharField(max_length=200, required=False, allow_blank=True, default='Resi Pembelian')
    pos_resi_judul_email = serializers.CharField(max_length=200, required=False, allow_blank=True, default='Resi Pembelian')
    pos_resi_catatan = serializers.CharField(required=False, allow_blank=True, default='Terima Kasih Atas Kunjungan Anda')
    pos_resi_sembunyikan_no_pesanan = serializers.BooleanField(required=False, default=False)

    # Email Laporan POS Settings
    pos_email_penerima = serializers.CharField(required=False, allow_blank=True, default='')
    pos_email_kirim_otomatis = serializers.BooleanField(required=False, default=False)

    # POS Pass Key Settings
    pos_passkey_belum_bayar_aktif = serializers.BooleanField(required=False, default=False)
    pos_passkey_belum_bayar_val = serializers.CharField(max_length=20, required=False, allow_blank=True, default='000000')
    pos_passkey_sudah_bayar_aktif = serializers.BooleanField(required=False, default=False)
    pos_passkey_sudah_bayar_val = serializers.CharField(max_length=20, required=False, allow_blank=True, default='000000')
    pos_passkey_diskon_aktif = serializers.BooleanField(required=False, default=False)
    pos_passkey_diskon_val = serializers.CharField(max_length=20, required=False, allow_blank=True, default='000000')
    pos_passkey_pelanggan_aktif = serializers.BooleanField(required=False, default=False)
    pos_passkey_pelanggan_val = serializers.CharField(max_length=20, required=False, allow_blank=True, default='000000')

    # POS Extended Settings
    pos_ext_settings = serializers.JSONField(required=False, default=dict)

    # POS Shift Settings
    pos_shift_aktif = serializers.BooleanField(required=False, default=False)
    pos_shift_kas_awal = serializers.IntegerField(required=False, default=0)
    pos_shift_sembunyikan_setor = serializers.BooleanField(required=False, default=False)
    pos_shift_cek_pesanan_tertahan = serializers.BooleanField(required=False, default=False)

    # POS Mode Cek Stok Settings
    pos_stok_blokir_jual_jika_kosong = serializers.BooleanField(required=False, default=True)
    pos_stok_selalu_cek_sebelum_order = serializers.BooleanField(required=False, default=False)
    pos_stok_blokir_hapus_jika_ada = serializers.BooleanField(required=False, default=False)
    pos_stok_transfer_harus_proses_penerima = serializers.BooleanField(required=False, default=False)
    pos_stok_posting_otomatis_laba_rugi = serializers.BooleanField(required=False, default=False)

    # POS Antrian Settings
    pos_antrian_aktif = serializers.BooleanField(required=False, default=False)

    # Metadata: daftar divisi (read-only)
    divisi_list     = serializers.SerializerMethodField()

    FIELD_KEY_MAP = {
        'nama_bisnis':  'bisnis_nama',
        'alamat':       'bisnis_alamat',
        'no_telepon':   'bisnis_no_telepon',
        'mata_uang':    'bisnis_mata_uang',
        'logo_url':     'bisnis_logo_url',
        'deskripsi':    'bisnis_deskripsi',
        
        # Keuangan & Invoice
        'ppn_default':   'bisnis_ppn_default',
        'invoice_terms': 'bisnis_invoice_terms',
        'order_prefix':  'bisnis_order_prefix',
        
        # Kebijakan Karyawan & Payroll
        'payroll_jam_masuk':       'payroll_jam_masuk',
        'payroll_jam_keluar':      'payroll_jam_keluar',
        'payroll_toleransi_menit': 'payroll_toleransi_menit',
        'biaya_potongan_terlambat': 'biaya_potongan_terlambat',
        
        # API & Integrasi Cloud
        'r2_bucket_name':   'api_r2_bucket_name',
        'r2_custom_domain': 'api_r2_custom_domain',
        'smtp_host':        'api_smtp_host',
        'smtp_port':        'api_smtp_port',
        'smtp_user':        'api_smtp_user',
        'smtp_password':    'api_smtp_password',
        
        # Catatan Resi POS Settings
        'pos_resi_judul':                  'pos_resi_judul',
        'pos_resi_judul_email':            'pos_resi_judul_email',
        'pos_resi_catatan':                'pos_resi_catatan',
        'pos_resi_sembunyikan_no_pesanan': 'pos_resi_sembunyikan_no_pesanan',
        
        # Email Laporan POS Settings
        'pos_email_penerima':              'pos_email_penerima',
        'pos_email_kirim_otomatis':        'pos_email_kirim_otomatis',

        # POS Pass Key Settings
        'pos_passkey_belum_bayar_aktif':   'pos_passkey_belum_bayar_aktif',
        'pos_passkey_belum_bayar_val':     'pos_passkey_belum_bayar_val',
        'pos_passkey_sudah_bayar_aktif':   'pos_passkey_sudah_bayar_aktif',
        'pos_passkey_sudah_bayar_val':     'pos_passkey_sudah_bayar_val',
        'pos_passkey_diskon_aktif':        'pos_passkey_diskon_aktif',
        'pos_passkey_diskon_val':          'pos_passkey_diskon_val',
        'pos_passkey_pelanggan_aktif':     'pos_passkey_pelanggan_aktif',
        'pos_passkey_pelanggan_val':       'pos_passkey_pelanggan_val',

        # POS Extended Settings
        'pos_ext_settings':                'pos_ext_settings',

        # POS Shift Settings
        'pos_shift_aktif':                 'pos_shift_aktif',
        'pos_shift_kas_awal':              'pos_shift_kas_awal',
        'pos_shift_sembunyikan_setor':      'pos_shift_sembunyikan_setor',
        'pos_shift_cek_pesanan_tertahan':  'pos_shift_cek_pesanan_tertahan',

        # POS Mode Cek Stok Settings
        'pos_stok_blokir_jual_jika_kosong':      'pos_stok_blokir_jual_jika_kosong',
        'pos_stok_selalu_cek_sebelum_order':     'pos_stok_selalu_cek_sebelum_order',
        'pos_stok_blokir_hapus_jika_ada':        'pos_stok_blokir_hapus_jika_ada',
        'pos_stok_transfer_harus_proses_penerima': 'pos_stok_transfer_harus_proses_penerima',
        'pos_stok_posting_otomatis_laba_rugi':   'pos_stok_posting_otomatis_laba_rugi',
        'pos_antrian_aktif':                     'pos_antrian_aktif',
    }

    def get_divisi_list(self, obj):
        return list(Divisi.objects.values('id', 'nama', 'keterangan'))

    def to_representation(self, instance):
        """Baca semua key bisnis dari SystemConfig."""
        result = {}
        defaults = {
            'ppn_default': 11,
            'invoice_terms': '',
            'order_prefix': 'ORD',
            'payroll_jam_masuk': '08:00',
            'payroll_jam_keluar': '17:00',
            'payroll_toleransi_menit': 15,
            'biaya_potongan_terlambat': 1000,
            'r2_bucket_name': '',
            'r2_custom_domain': '',
            'smtp_host': '',
            'smtp_port': 587,
            'smtp_user': '',
            'smtp_password': '',
            
            # Catatan Resi Defaults
            'pos_resi_judul': 'Resi Pembelian',
            'pos_resi_judul_email': 'Resi Pembelian',
            'pos_resi_catatan': 'Terima Kasih Atas Kunjungan Anda',
            'pos_resi_sembunyikan_no_pesanan': False,
            
            # Email Laporan Defaults
            'pos_email_penerima': '',
            'pos_email_kirim_otomatis': False,

            # POS Pass Key Defaults
            'pos_passkey_belum_bayar_aktif': False,
            'pos_passkey_belum_bayar_val': '000000',
            'pos_passkey_sudah_bayar_aktif': False,
            'pos_passkey_sudah_bayar_val': '000000',
            'pos_passkey_diskon_aktif': False,
            'pos_passkey_diskon_val': '000000',
            'pos_passkey_pelanggan_aktif': False,
            'pos_passkey_pelanggan_val': '000000',

            # POS Extended Settings Defaults
            'pos_ext_settings': {
                'hide_packet_item_resi': False,
                'merge_qty_item_resi': False,
                'print_total_qty_resi': False,
                'print_note_item_resi': False,
                'pos_custom_resi_windows': False,
                'disable_print_checking': False,
                'disable_reprint': False,
                'disable_drawer_reprint': False,
                'disable_hold_queue': False,
                'hide_other_device_online_tx': False,
                'disable_auto_change_qty_view': False,
                'disable_add_cash_io_type': False,
                'enable_waiter_tracking': False,
                'block_sell_less_than_buy_price': False,
                'credit_payment_check_balance': False,
                'disable_add_custom_item': False,
                'staff_only_see_same_day_tx': False,
                'hide_remaining_stock': False,
                'hide_customer_list': False,
                'disable_dine_in_take_away': False,
                'must_select_table': False,
                'kitchen_print_normal_font': False,
                'block_same_order_multi_waiter': False,
                'enable_take_feature': False,
                'enable_paper_saving': False,
                'order_no_reset_daily': False,
                'hide_splitbill': False,
                'allow_offline_tx': True,
                'allow_backdate_online_tx': True,
                'log_cancelled_pos_items': True,
                'different_customers_same_no': False,
            },

            # POS Shift Defaults
            'pos_shift_aktif': False,
            'pos_shift_kas_awal': 0,
            'pos_shift_sembunyikan_setor': False,
            'pos_shift_cek_pesanan_tertahan': False,

            # POS Mode Cek Stok Defaults
            'pos_stok_blokir_jual_jika_kosong': True,
            'pos_stok_selalu_cek_sebelum_order': False,
            'pos_stok_blokir_hapus_jika_ada': False,
            'pos_stok_transfer_harus_proses_penerima': False,
            'pos_stok_posting_otomatis_laba_rugi': False,

            # POS Antrian Defaults
            'pos_antrian_aktif': False,
        }
        
        for field_name, config_key in self.FIELD_KEY_MAP.items():
            try:
                val = SystemConfig.objects.get(key=config_key).value
                if field_name in ['ppn_default', 'payroll_toleransi_menit', 'biaya_potongan_terlambat', 'smtp_port', 'pos_shift_kas_awal']:
                    try:
                        result[field_name] = int(val)
                    except ValueError:
                        result[field_name] = defaults.get(field_name, 0)
                elif field_name in [
                    'pos_resi_sembunyikan_no_pesanan', 'pos_email_kirim_otomatis',
                    'pos_passkey_belum_bayar_aktif', 'pos_passkey_sudah_bayar_aktif',
                    'pos_passkey_diskon_aktif', 'pos_passkey_pelanggan_aktif',
                    'pos_shift_aktif', 'pos_shift_sembunyikan_setor', 'pos_shift_cek_pesanan_tertahan',
                    'pos_stok_blokir_jual_jika_kosong', 'pos_stok_selalu_cek_sebelum_order',
                    'pos_stok_blokir_hapus_jika_ada', 'pos_stok_transfer_harus_proses_penerima',
                    'pos_stok_posting_otomatis_laba_rugi', 'pos_antrian_aktif'
                ]:
                    result[field_name] = (val.lower() == 'true')
                elif field_name == 'pos_ext_settings':
                    import json
                    try:
                        loaded = json.loads(val)
                        merged = defaults.get('pos_ext_settings', {}).copy()
                        if isinstance(loaded, dict):
                            merged.update(loaded)
                        result[field_name] = merged
                    except (ValueError, TypeError):
                        result[field_name] = defaults.get('pos_ext_settings', {})
                else:
                    result[field_name] = val
            except SystemConfig.DoesNotExist:
                result[field_name] = defaults.get(field_name, '')
        
        # Resolve logo_url to absolute URL if it is a relative path
        logo_val = result.get('logo_url')
        if logo_val:
            if not (logo_val.startswith('http://') or logo_val.startswith('https://')):
                from django.core.files.storage import default_storage
                try:
                    url = default_storage.url(logo_val)
                    if url.startswith('http://') or url.startswith('https://'):
                        result['logo_url'] = url
                    else:
                        request = self.context.get('request')
                        if request:
                            result['logo_url'] = request.build_absolute_uri(url)
                        else:
                            result['logo_url'] = url
                except Exception as e:
                    logger.exception("Gagal mendapatkan logo URL")

        result['divisi_list'] = self.get_divisi_list(instance)
        return result

    def save(self):
        """Tulis perubahan ke SystemConfig."""
        data = self.validated_data
        
        # Handle file upload logo_file
        logo_file = data.get('logo_file')
        if logo_file:
            from django.core.files.storage import default_storage
            import os
            import uuid
            
            # Buat nama file unik
            ext = os.path.splitext(logo_file.name)[1].lower()
            if not ext:
                ext = '.png'
            filename = f"business_logo/logo_{uuid.uuid4().hex[:8]}{ext}"
            
            # Hapus file lama jika ada
            try:
                old_logo = SystemConfig.objects.get(key='bisnis_logo_url').value
                if old_logo and not (old_logo.startswith('http://') or old_logo.startswith('https://')):
                    # Bersihkan path dari MEDIA_URL jika ada
                    clean_path = old_logo
                    from django.conf import settings
                    if clean_path.startswith(settings.MEDIA_URL):
                        clean_path = clean_path[len(settings.MEDIA_URL):]
                    if default_storage.exists(clean_path):
                        default_storage.delete(clean_path)
            except Exception as e:
                logger.exception("Gagal menghapus logo lama")
                
            # Simpan file baru
            path = default_storage.save(filename, logo_file)
            SystemConfig.objects.update_or_create(
                key='bisnis_logo_url',
                defaults={'value': path}
            )

        for field_name, config_key in self.FIELD_KEY_MAP.items():
            if field_name in data and field_name != 'logo_file':
                # Jika upload logo_file dilakukan, jangan timpa bisnis_logo_url dengan logo_url kosong/lama yang dikirim
                if field_name == 'logo_url' and logo_file:
                    continue
                
                value_to_save = data[field_name]
                if field_name == 'logo_url' and value_to_save:
                    from django.conf import settings
                    media_url = settings.MEDIA_URL  # '/media/'
                    if value_to_save.startswith('http://') or value_to_save.startswith('https://'):
                        if media_url in value_to_save:
                            parts = value_to_save.split(media_url, 1)
                            if len(parts) > 1:
                                value_to_save = parts[1]
                    elif value_to_save.startswith(media_url):
                        value_to_save = value_to_save[len(media_url):]

                # Simpan sebagai string ke TextField
                if field_name == 'pos_ext_settings':
                    import json
                    val_str = json.dumps(value_to_save)
                elif field_name in [
                    'pos_resi_sembunyikan_no_pesanan', 'pos_email_kirim_otomatis',
                    'pos_passkey_belum_bayar_aktif', 'pos_passkey_sudah_bayar_aktif',
                    'pos_passkey_diskon_aktif', 'pos_passkey_pelanggan_aktif',
                    'pos_shift_aktif', 'pos_shift_sembunyikan_setor', 'pos_shift_cek_pesanan_tertahan',
                    'pos_stok_blokir_jual_jika_kosong', 'pos_stok_selalu_cek_sebelum_order',
                    'pos_stok_blokir_hapus_jika_ada', 'pos_stok_transfer_harus_proses_penerima',
                    'pos_stok_posting_otomatis_laba_rugi', 'pos_antrian_aktif'
                ]:
                    val_str = 'true' if value_to_save else 'false'
                else:
                    val_str = str(value_to_save) if value_to_save is not None else ''
                SystemConfig.objects.update_or_create(
                    key=config_key,
                    defaults={'value': val_str}
                )
        return data


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = '__all__'


# --- 10. Komplain & Garansi Serializers ---
class KomplainLogSerializer(serializers.ModelSerializer):
    user_nama = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = KomplainLog
        fields = ['id', 'user', 'user_nama', 'status_baru', 'catatan', 'waktu']
        read_only_fields = ['user', 'waktu']


class KomplainOrderSerializer(serializers.ModelSerializer):
    dicatat_oleh_nama  = serializers.ReadOnlyField(source='dicatat_oleh.username')
    ditangani_oleh_nama = serializers.ReadOnlyField(source='ditangani_oleh.username')
    order_nama         = serializers.ReadOnlyField(source='order.nama')
    order_nomor_wa     = serializers.ReadOnlyField(source='order.nomor_wa')
    jenis_display      = serializers.SerializerMethodField()
    status_display     = serializers.SerializerMethodField()
    resolusi_display   = serializers.SerializerMethodField()
    logs               = KomplainLogSerializer(many=True, read_only=True)

    class Meta:
        model = KomplainOrder
        fields = [
            'id', 'order', 'order_nama', 'order_nomor_wa',
            'dicatat_oleh', 'dicatat_oleh_nama',
            'ditangani_oleh', 'ditangani_oleh_nama',
            'jenis_komplain', 'jenis_display',
            'deskripsi', 'status', 'status_display',
            'resolusi', 'resolusi_display',
            'catatan_resolusi', 'perlu_cetak_ulang',
            'foto_bukti', 'waktu_masuk', 'waktu_selesai',
            'logs',
        ]
        read_only_fields = ['waktu_masuk', 'waktu_selesai']

    def get_jenis_display(self, obj):
        return obj.get_jenis_komplain_display()

    def get_status_display(self, obj):
        return obj.get_status_display()

    def get_resolusi_display(self, obj):
        return obj.get_resolusi_display() if obj.resolusi else None


# --- 11. CRM & MRP Serializers ---
class CustomerActivitySerializer(serializers.ModelSerializer):
    pic_username = serializers.ReadOnlyField(source='pic.username')
    order_nama = serializers.ReadOnlyField(source='order.nama')

    class Meta:
        model = CustomerActivity
        fields = '__all__'
        read_only_fields = ['pic']


class BoMItemSerializer(serializers.ModelSerializer):
    inventory_item_nama = serializers.ReadOnlyField(source='inventory_item.nama')
    inventory_item_satuan = serializers.ReadOnlyField(source='inventory_item.satuan')

    class Meta:
        model = BoMItem
        fields = '__all__'


class BillOfMaterialsSerializer(serializers.ModelSerializer):
    product_nama = serializers.ReadOnlyField(source='product.nama_produk')
    product_material = serializers.ReadOnlyField(source='product.material')
    items = BoMItemSerializer(many=True, read_only=True)

    class Meta:
        model = BillOfMaterials
        fields = '__all__'


class POSAntrianDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSAntrianDevice
        fields = '__all__'


class SaldoKasHarianSerializer(serializers.ModelSerializer):
    kasir_nama = serializers.SerializerMethodField()

    class Meta:
        model = SaldoKasHarian
        fields = '__all__'

    def get_kasir_nama(self, obj):
        if obj.kasir:
            name = f"{obj.kasir.first_name} {obj.kasir.last_name}".strip()
            return name if name else obj.kasir.username
        return ""


class RingkasanShiftSerializer(serializers.ModelSerializer):
    kasir_nama = serializers.SerializerMethodField()

    class Meta:
        model = RingkasanShift
        fields = '__all__'

    def get_kasir_nama(self, obj):
        if obj.kasir:
            name = f"{obj.kasir.first_name} {obj.kasir.last_name}".strip()
            return name if name else obj.kasir.username
        return ""