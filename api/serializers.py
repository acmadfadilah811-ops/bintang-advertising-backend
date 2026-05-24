from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
# FIX: AppConfig diganti SystemConfig di models.py
from .models import Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard, InventoryItem, RestockHistory, ProductPrice, SystemConfig, FAQ

# --- 1. Master Data Serializers ---
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
            'id', 'username', 'role', 'divisi', 'divisi_nama', 'no_hp', 'kota', 
            'negara', 'alamat', 'bio', 'foto_profil', 'last_login', 'date_joined', 
            'status_karyawan', 'jenis_kontrak', 'kontrak_mulai', 'kontrak_selesai',
            'no_kpj', 'bpjs_kes', 'file_pkwt'
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
        fields = ['username', 'password', 'role', 'divisi', 'no_hp'] 
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
            no_hp=validated_data.get('no_hp', '')
        )
        # Hash password sebelum disimpan
        user.set_password(validated_data['password'])
        user.save()
        return user

# --- 3. JobBoard Serializer ---
class JobBoardSerializer(serializers.ModelSerializer):
    tahap_nama        = serializers.ReadOnlyField(source='tahap.nama')
    tahap_divisi_nama = serializers.ReadOnlyField(source='tahap.divisi.nama')  # Untuk grouping per divisi
    pic_nama          = serializers.ReadOnlyField(source='pic_staff.username')
    pic_divisi_nama   = serializers.ReadOnlyField(source='pic_staff.divisi.nama') # Fallback grouping
    
    class Meta:
        model = JobBoard
        fields = '__all__'

# --- 4. Order Item Serializer (Detail Pecahan) ---
class OrderItemSerializer(serializers.ModelSerializer):
    jobs = JobBoardSerializer(many=True, read_only=True) # Nested JobBoard

    class Meta:
        model = OrderItem
        fields = '__all__'
        read_only_fields = ['luas'] # Luas otomatis dihitung oleh backend, front-end dilarang kirim data ini

# --- 5. Order Serializer (Induk Nota) ---
class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'waktu', 'nomor_wa', 'nama', 'status_global', 'catatan_pelanggan', 
            'items', 'dp_dibayar', 'diskon_persen', 'total_harga', 'sisa_tagihan', 'metode_pembayaran'
        ]
        extra_kwargs = {
            'id': {'read_only': True},       
            'waktu': {'required': False},    
            'total_harga': {'read_only': True},  # Biarkan backend yang menjumlahkan totalnya
            'sisa_tagihan': {'read_only': True}, # Backend auto kurang total dengan DP
        }

# --- 6. Contact & Pendukung ---
class ContactSerializer(serializers.ModelSerializer):
    total_piutang = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = ['nomor_wa', 'nama', 'total_order', 'total_spent', 'last_order', 'keterangan', 'total_piutang']

    def get_total_piutang(self, obj):
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
    deskripsi       = serializers.CharField(required=False, allow_blank=True)
    # Metadata: daftar divisi (read-only)
    divisi_list     = serializers.SerializerMethodField()

    FIELD_KEY_MAP = {
        'nama_bisnis':  'bisnis_nama',
        'alamat':       'bisnis_alamat',
        'no_telepon':   'bisnis_no_telepon',
        'mata_uang':    'bisnis_mata_uang',
        'logo_url':     'bisnis_logo_url',
        'deskripsi':    'bisnis_deskripsi',
    }

    def get_divisi_list(self, obj):
        return list(Divisi.objects.values('id', 'nama', 'keterangan'))

    def to_representation(self, instance):
        """Baca semua key bisnis dari SystemConfig."""
        result = {}
        for field_name, config_key in self.FIELD_KEY_MAP.items():
            try:
                result[field_name] = SystemConfig.objects.get(key=config_key).value
            except SystemConfig.DoesNotExist:
                result[field_name] = ''
        result['divisi_list'] = self.get_divisi_list(instance)
        return result

    def save(self):
        """Tulis perubahan ke SystemConfig."""
        data = self.validated_data
        for field_name, config_key in self.FIELD_KEY_MAP.items():
            if field_name in data:
                SystemConfig.objects.update_or_create(
                    key=config_key,
                    defaults={'value': data[field_name]}
                )
        return data


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = '__all__'