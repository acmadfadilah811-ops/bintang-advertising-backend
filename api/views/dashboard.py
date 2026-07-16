import calendar
from datetime import timedelta
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Sum, Count, Q, F
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from ..models import Order, OrderItem, JobBoard, CustomUser, InventoryItem

class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cache_key = "dashboard_data"
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data)

        # Ambil waktu sekarang sesuai timezone lokal
        now = timezone.localtime(timezone.now())
        
        # 1. Rentang waktu hari ini
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        
        # 2. Rentang waktu bulan ini
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Cari hari terakhir di bulan ini
        _, last_day = calendar.monthrange(now.year, now.month)
        month_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)

        # --- Total Order ---
        # Ganti __date dan __month dengan rentang (__gte, __lt) agar aman di SQLite
        total_order_hari_ini = Order.objects.filter(waktu__gte=today_start, waktu__lt=today_end).count()
        total_order_bulan_ini = Order.objects.filter(waktu__gte=month_start, waktu__lte=month_end).count()

        # --- Omset Bulan Ini ---
        omset_bulan_ini = OrderItem.objects.filter(
            order__waktu__gte=month_start,
            order__waktu__lte=month_end
        ).aggregate(total=Sum('harga_jual'))['total'] or 0

        # --- Omset 6 Bulan Terakhir (Untuk Grafik) ---
        omset_6_bulan = []
        for i in range(5, -1, -1):
            target_month = now.month - i
            target_year = now.year
            if target_month <= 0:
                target_month += 12
                target_year -= 1
                
            # Tentukan rentang waktu untuk bulan target
            m_start = now.replace(year=target_year, month=target_month, day=1, hour=0, minute=0, second=0, microsecond=0)
            _, m_last_day = calendar.monthrange(target_year, target_month)
            m_end = now.replace(year=target_year, month=target_month, day=m_last_day, hour=23, minute=59, second=59, microsecond=999999)

            nama_bulan = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agt", "Sep", "Okt", "Nov", "Des"][target_month - 1]
            
            total = OrderItem.objects.filter(
                order__waktu__gte=m_start,
                order__waktu__lte=m_end
            ).aggregate(t=Sum('harga_jual'))['t'] or 0
            
            omset_6_bulan.append({
                'bulan': f"{nama_bulan}",
                'total': total
            })

        # --- Distribusi Status Order ---
        order_per_status = {
            s: Order.objects.filter(status_global=s).count()
            for s in ['review', 'desain', 'proses', 'ready', 'selesai', 'batal']
        }

        # --- Distribusi Status Job ---
        job_per_status = {
            s: JobBoard.objects.filter(status_pekerjaan=s).count()
            for s in ['antrean', 'dikerjakan', 'selesai', 'gagal', 'kendala']
        }

        # --- Top Staff (berdasarkan jumlah job selesai) ---
        top_staff_qs = CustomUser.objects.filter(role='staff').annotate(
            jumlah_job_selesai=Count(
                'my_tasks', filter=Q(my_tasks__status_pekerjaan='selesai')
            ),
            total_insentif=Sum(
                'my_tasks__insentif', filter=Q(my_tasks__status_pekerjaan='selesai')
            )
        ).order_by('-jumlah_job_selesai')[:5]

        top_staff = [
            {
                'nama': u.username,
                'jumlah_job_selesai': u.jumlah_job_selesai,
                'total_insentif': u.total_insentif or 0,
            }
            for u in top_staff_qs
        ]

        # --- Stok Kritis (stok di bawah minimum) ---
        stok_kritis_qs = InventoryItem.objects.filter(stok__lt=F('min_stok'))
        stok_kritis = [
            {
                'nama': item.nama,
                'stok': item.stok,
                'min_stok': item.min_stok,
                'satuan': item.satuan,
            }
            for item in stok_kritis_qs
        ]

        data = {
            'total_order_hari_ini': total_order_hari_ini,
            'total_order_bulan_ini': total_order_bulan_ini,
            'omset_bulan_ini': omset_bulan_ini,
            'omset_6_bulan': omset_6_bulan,
            'order_per_status': order_per_status,
            'job_per_status': job_per_status,
            'top_staff': top_staff,
            'stok_kritis': stok_kritis,
        }
        cache.set(cache_key, data, timeout=300)
        return Response(data)
