# ruff: noqa: E402
"""
Script reset: hapus semua data Order, OrderItem, JobBoard
Jalankan: uv run python reset_data.py
"""
import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bintang_project.settings')
django.setup()

from api.models import JobBoard, OrderItem, Order

j = JobBoard.objects.all().delete()
i = OrderItem.objects.all().delete()
o = Order.objects.all().delete()

print("[SUCCESS] Database berhasil direset!")
print(f"   - JobBoard  dihapus: {j[0]} record")
print(f"   - OrderItem dihapus: {i[0]} record")
print(f"   - Order     dihapus: {o[0]} record")
print("\nData User, Divisi, TahapProses tetap aman.")
