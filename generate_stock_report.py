import os
import django
import datetime
import openpyxl

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from api.product_models import Product, ProductVariant, ProductStockMovement
from django.utils.dateparse import parse_date

def run():
    start_date = parse_date('2026-07-02')
    end_date = parse_date('2026-07-02')
    
    target_path = r"d:\buku zis\summary-2026-07-02__2026-07-02 (1).xlsx"
    print(f"Generating stock report for {start_date} to {end_date} and saving to {target_path}...")
    
    # Gather products and variants
    products = Product.objects.all().select_related('kategori').prefetch_related('variants')
    
    skus = {}
    for p in products:
        if p.has_variant and p.variants.exists():
            for v in p.variants.all():
                skus[(p.id, v.id)] = {
                    'product_id': p.id,
                    'variant_id': v.id,
                    'product_name': p.nama,
                    'variant_name': v.nama_varian,
                    'sku': v.sku or p.sku or '',
                    'group': p.kategori.nama if p.kategori else 'Umum',
                    'current_qty': v.qty_stok,
                }
        else:
            skus[(p.id, None)] = {
                'product_id': p.id,
                'variant_id': None,
                'product_name': p.nama,
                'variant_name': '',
                'sku': p.sku or '',
                'group': p.kategori.nama if p.kategori else 'Umum',
                'current_qty': p.qty_stok,
            }
            
    movements = ProductStockMovement.objects.all().order_by('created_at')
    
    summary_map = {}
    for key, info in skus.items():
        summary_map[key] = {
            'group': info['group'],
            'product': f"{info['product_name']} - {info['variant_name']}" if info['variant_name'] else info['product_name'],
            'initial': float(info['current_qty']),
            'in': 0.0,
            'returnStock': 0.0,
            'sales': 0.0,
            'out': 0.0,
            'sisa': float(info['current_qty']),
            'movements_before': [],
            'movements_during': [],
        }
        
    for m in movements:
        m_date = m.tanggal or m.created_at.date()
        key = (m.product_id, m.variant_id)
        if key not in summary_map:
            continue
            
        if m_date < start_date:
            summary_map[key]['movements_before'].append(m)
        elif start_date <= m_date <= end_date:
            summary_map[key]['movements_during'].append(m)
            
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pergerakan Stok"
    
    headers = ['Grup', 'Produk', 'Awal', 'Masuk', 'Pengembalian', 'Penjualan', 'Keluar', 'Sisa']
    ws.append(headers)
    
    for key, s in summary_map.items():
        info = skus[key]
        
        # Initial Stock (Awal)
        if s['movements_before']:
            last_before = s['movements_before'][-1]
            initial_val = last_before.stok_akhir
        elif s['movements_during']:
            first_during = s['movements_during'][0]
            initial_val = first_during.stok_awal
        else:
            movements_after = ProductStockMovement.objects.filter(
                product_id=info['product_id'],
                variant_id=info['variant_id']
            ).order_by('created_at')
            after_list = []
            for m in movements_after:
                m_date = m.tanggal or m.created_at.date()
                if m_date > end_date:
                    after_list.append(m)
            if after_list:
                initial_val = after_list[0].stok_awal
            else:
                initial_val = info['current_qty']
                
        s['initial'] = float(initial_val)
        
        # Mutation during period
        in_qty = 0.0
        out_qty = 0.0
        sales_qty = 0.0
        return_qty = 0.0
        
        for m in s['movements_during']:
            qty = float(m.qty)
            if m.tipe in ('masuk', 'produksi'):
                in_qty += qty
            elif m.tipe == 'keluar':
                out_qty += qty
            elif m.tipe == 'penjualan':
                sales_qty += qty
            elif m.tipe == 'pengembalian':
                return_qty += qty
                
        s['in'] = in_qty
        s['out'] = out_qty
        s['sales'] = sales_qty
        s['returnStock'] = return_qty
        
        # Sisa
        if s['movements_during']:
            sisa_val = s['movements_during'][-1].stok_akhir
        elif s['movements_before']:
            sisa_val = s['movements_before'][-1].stok_akhir
        else:
            sisa_val = initial_val
            
        s['sisa'] = float(sisa_val)
        
        ws.append([
            s['group'],
            s['product'],
            s['initial'],
            s['in'],
            s['returnStock'],
            s['sales'],
            s['out'],
            s['sisa'],
        ])
        
    wb.save(target_path)
    print("Excel report successfully created at", target_path)

if __name__ == '__main__':
    run()
