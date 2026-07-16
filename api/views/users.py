from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from ..models import CustomUser, Divisi as DivisiModel
from ..serializers import CustomUserSerializer
from ..permissions import IsOwnerManagerOrAdmin, IsOwnerOrManager
from users.models import SecurityAuditLog

class CustomUserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    
    def get_permissions(self):
        if self.action == 'me':
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsOwnerManagerOrAdmin()]

    def check_permissions(self, request):
        super().check_permissions(request)
        # Proteksi hak akses: Staff tidak boleh mengubah data karyawan lain
        # Hanya Owner, Manager, atau Admin yang dapat memodifikasi model karyawan secara umum
        if request.method not in ['GET', 'HEAD', 'OPTIONS']:
            if self.action != 'me':
                if not (request.user and getattr(request.user, 'role', '') in ['owner', 'manager', 'admin']):
                    self.permission_denied(request, message="Hanya Owner, Manager, atau Admin yang dapat memodifikasi data karyawan.")

    def get_queryset(self):
        queryset = CustomUser.objects.all()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    @action(detail=False, methods=['get', 'patch'], url_path='me')
    def me(self, request):
        user = request.user
        if request.method == 'GET':
            serializer = self.get_serializer(user)
            return Response(serializer.data)
        elif request.method == 'PATCH':
            # Proteksi field HR/Admin agar tidak bisa diubah oleh staff secara mandiri
            if hasattr(request.data, '_mutable'):
                data = request.data.copy()
            elif isinstance(request.data, dict):
                data = request.data.copy()
            else:
                data = dict(request.data)

            if request.user.role not in ['owner', 'manager', 'admin']:
                hr_fields = [
                    'username', 'email', 'role', 'divisi', 'status_karyawan', 
                    'jenis_kontrak', 'kontrak_mulai', 'kontrak_selesai', 
                    'no_kpj', 'bpjs_kes', 'file_pkwt', 'nip'
                ]
                for field in hr_fields:
                    if field in data:
                        data.pop(field)

            serializer = self.get_serializer(user, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=400)

    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        # Hanya Owner atau Manager yang boleh mereset password
        if request.user.role not in ['owner', 'manager']:
            return Response({'error': 'Hanya Owner atau Manager yang dapat mereset password.'}, status=status.HTTP_403_FORBIDDEN)
        
        user_to_reset = self.get_object()
        
        # Manager tidak boleh mereset password Owner atau Manager lain
        if request.user.role == 'manager' and user_to_reset.role in ['owner', 'manager']:
            return Response({'error': 'Manager tidak boleh mereset password Owner atau Manager.'}, status=status.HTTP_403_FORBIDDEN)
            
        new_password = request.data.get('new_password')
        if not new_password:
            return Response({'error': 'Password baru wajib diisi.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            validate_password(new_password, user=user_to_reset)
        except DjangoValidationError as e:
            return Response({'error': ", ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)
            
        user_to_reset.set_password(new_password)
        user_to_reset.save()
        
        # Catat audit log
        SecurityAuditLog.objects.create(
            user=request.user,
            username_input=request.user.username,
            event="PASSWORD_CHANGED",
            ip_address=request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip(),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            keterangan=f"Reset password untuk user {user_to_reset.username} oleh {request.user.role}",
            berhasil=True,
        )
        
        return Response({'message': f'Password untuk {user_to_reset.username} berhasil diubah.'}, status=status.HTTP_200_OK)


class CreateUserView(APIView):
    permission_classes = [IsOwnerOrManager]

    def post(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '').strip()
        role     = request.data.get('role', 'staff')
        no_hp    = request.data.get('no_hp', '')
        divisi   = request.data.get('divisi', None)
        first_name = request.data.get('first_name', '')

        # Validasi field wajib
        if not username or not password:
            return Response(
                {'error': 'Username dan password wajib diisi.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Cek duplikat username
        if CustomUser.objects.filter(username=username).exists():
            return Response(
                {'error': f'Username "{username}" sudah digunakan.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Buat user baru
        user = CustomUser(
            username=username,
            role=role,
            no_hp=no_hp,
            first_name=first_name,
        )
        if divisi:
            try:
                user.divisi = DivisiModel.objects.get(pk=divisi)
            except DivisiModel.DoesNotExist:
                pass

        user.set_password(password)  # Hash password dengan benar
        user.save()

        return Response(
            {
                'message': f'Akun "{username}" berhasil dibuat.',
                'id': user.id,
                'username': user.username,
                'role': user.role,
            },
            status=status.HTTP_201_CREATED
        )
