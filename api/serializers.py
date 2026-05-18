from rest_framework import serializers
from .models import Divisi, TahapProses, CustomUser, Contact, Order, OrderItem, JobBoard, InventoryItem, RestockHistory, ProductPrice, AppConfig, FAQ

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
        fields = ['id', 'username', 'role', 'divisi', 'divisi_nama', 'no_hp', 'kota', 'negara', 'alamat', 'bio', 'foto_profil', 'last_login', 'date_joined']

# Endpoint B-05: Serializer Khusus Pembuatan User dengan Hash Password
class CreateUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['username', 'password', 'role', 'divisi', 'no_hp'] 
        extra_kwargs = {
            'password': {'write_only': True}
        }

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
    tahap_nama       = serializers.ReadOnlyField(source='tahap.nama')
    tahap_divisi_nama = serializers.ReadOnlyField(source='tahap.divisi.nama')  # Untuk grouping per divisi
    pic_nama         = serializers.ReadOnlyField(source='pic_staff.username')
    pic_divisi_nama  = serializers.ReadOnlyField(source='pic_staff.divisi.nama') # Fallback grouping
    
    class Meta:
        model = JobBoard
        fields = '__all__'

# --- 4. Order Item Serializer (Detail Pecahan) ---
class OrderItemSerializer(serializers.ModelSerializer):
    jobs = JobBoardSerializer(many=True, read_only=True) # Nested JobBoard

    class Meta:
        model = OrderItem
        fields = '__all__'

# --- 5. Order Serializer (Induk Nota) ---
class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'waktu', 'nomor_wa', 'nama', 'status_global', 'catatan_pelanggan', 'items']
        extra_kwargs = {
            'id': {'read_only': True},       # ID di-generate otomatis oleh backend
            'waktu': {'read_only': True},    # Timestamp otomatis saat create
        }

# --- 6. Contact & Pendukung ---
class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ['nomor_wa', 'nama', 'total_order', 'total_spent', 'last_order', 'keterangan']

# --- 5. Inventory Serializer ---
class RestockHistorySerializer(serializers.ModelSerializer):
    user_nama = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = RestockHistory
        fields = ['id', 'delta', 'stok_awal', 'stok_akhir', 'keterangan', 'waktu', 'user_nama']

class InventoryItemSerializer(serializers.ModelSerializer):
    # id auto-generate di backend, tidak perlu dikirim client
    # read_only=True → selalu ada di response, tidak perlu dari input
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

class AppConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppConfig
        fields = '__all__'

class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = '__all__'