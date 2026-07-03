from rest_framework import serializers
from .customer_models import (
    CustomerGroup, Customer, CustomerNote, CustomerNoteEntry, CustomerNoteDocument,
    CustomerNoteTag, CustomerReview, Supplier,
)

MAX_NOTE_DOCUMENTS = 5
MAX_NOTE_DOCUMENT_SIZE = 5 * 1024 * 1024


class CustomerGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerGroup
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']


class CustomerSerializer(serializers.ModelSerializer):
    customer_group_nama = serializers.ReadOnlyField(source='customer_group.nama')
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Customer
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']


class CustomerNoteTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerNoteTag
        fields = '__all__'


class CustomerNoteEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerNoteEntry
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']


class CustomerNoteDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerNoteDocument
        fields = '__all__'

    def validate_file(self, value):
        if value.size > MAX_NOTE_DOCUMENT_SIZE:
            raise serializers.ValidationError('Ukuran file maksimal 5MB.')
        content_type = getattr(value, 'content_type', '') or ''
        if not (content_type.startswith('image/') or content_type == 'application/pdf'):
            raise serializers.ValidationError('File harus berupa gambar atau PDF.')
        return value

    def validate_note(self, note):
        if note.documents.count() >= MAX_NOTE_DOCUMENTS:
            raise serializers.ValidationError(f'Maksimal {MAX_NOTE_DOCUMENTS} dokumen per catatan.')
        return note

    def create(self, validated_data):
        validated_data.setdefault('original_name', getattr(validated_data.get('file'), 'name', ''))
        return super().create(validated_data)


class CustomerNoteSerializer(serializers.ModelSerializer):
    tags = serializers.SerializerMethodField()
    tag_names = serializers.ListField(child=serializers.CharField(), write_only=True, required=False)
    entries = CustomerNoteEntrySerializer(many=True, read_only=True)
    documents = CustomerNoteDocumentSerializer(many=True, read_only=True)

    class Meta:
        model = CustomerNote
        fields = '__all__'
        read_only_fields = ['dibuat_oleh', 'customer_name']

    def get_tags(self, obj):
        return [t.nama for t in obj.tags.all()]

    def _sync_tags(self, note, tag_names):
        tags = []
        for name in tag_names:
            name = (name or '').strip()
            if not name:
                continue
            tag, _ = CustomerNoteTag.objects.get_or_create(nama=name)
            tags.append(tag)
        note.tags.set(tags)

    def create(self, validated_data):
        tag_names = validated_data.pop('tag_names', [])
        customer = validated_data.get('customer')
        validated_data['customer_name'] = customer.nama if customer else ''
        note = CustomerNote.objects.create(**validated_data)
        self._sync_tags(note, tag_names)
        return note

    def update(self, instance, validated_data):
        tag_names = validated_data.pop('tag_names', None)
        if 'customer' in validated_data:
            customer = validated_data['customer']
            validated_data['customer_name'] = customer.nama if customer else ''
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if tag_names is not None:
            self._sync_tags(instance, tag_names)
        return instance


class CustomerReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerReview
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'
        read_only_fields = ['dibuat_oleh']
