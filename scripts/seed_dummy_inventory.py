import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()


from api.models import InventoryItem, ProductPrice, BillOfMaterials, BoMItem

def seed():
    print("Seeding dummy inventory data...")
    
    # 1. Seed Inventory Items
    items_data = [
        {
            "id": "INV-TEST-FLX280",
            "nama": "Banner Flexi 280gr",
            "stok": 500.0,
            "satuan": "m2",
            "kategori": "Bahan Outdoor",
            "min_stok": 50.0,
            "cost_per_unit": 10000.0,
            "supplier": "CV Indo Flexindo"
        },
        {
            "id": "INV-TEST-IVR260",
            "nama": "Kertas Ivory 260gr A3+",
            "stok": 1000.0,
            "satuan": "lembar",
            "kategori": "Kertas",
            "min_stok": 100.0,
            "cost_per_unit": 2000.0,
            "supplier": "PT Surya Kertas"
        },
        {
            "id": "INV-TEST-SOLCYN",
            "nama": "Tinta Solvent Cyan",
            "stok": 5000.0,
            "satuan": "ml",
            "kategori": "Tinta",
            "min_stok": 500.0,
            "cost_per_unit": 100.0,
            "supplier": "CV Ink Solution"
        },
        {
            "id": "INV-TEST-YSNBLK",
            "nama": "Buku Yasin Softcover Blank",
            "stok": 200.0,
            "satuan": "pcs",
            "kategori": "Buku",
            "min_stok": 20.0,
            "cost_per_unit": 5000.0,
            "supplier": "Toko Buku Semesta"
        }
    ]

    inventory_items = {}
    for item in items_data:
        obj, created = InventoryItem.objects.update_or_create(
            id=item["id"],
            defaults=item
        )
        inventory_items[item["id"]] = obj
        print(f"  - InventoryItem: {obj.nama} ({'Dibuat' if created else 'Diperbarui'})")

    # 2. Seed Product Prices
    products_data = [
        {
            "kategori": "Outdoor",
            "nama_produk": "Banner MMT",
            "harga": 25000,
            "material": "Banner 280gr Best",
            "price_type": "flat"
        },
        {
            "kategori": "Buku",
            "nama_produk": "yasin soft cover",
            "harga": 15000,
            "material": "Art Paper 150",
            "price_type": "flat"
        },
        {
            "kategori": "Indoor",
            "nama_produk": "Kartu Nama",
            "harga": 35000,
            "material": "Ivory 260",
            "price_type": "flat"
        }
    ]

    products = {}
    for prod in products_data:
        obj, created = ProductPrice.objects.update_or_create(
            kategori=prod["kategori"],
            nama_produk=prod["nama_produk"],
            material=prod["material"],
            defaults=prod
        )
        products[f"{prod['nama_produk']}_{prod['material']}"] = obj
        print(f"  - ProductPrice: {obj.nama_produk} ({'Dibuat' if created else 'Diperbarui'})")

    # 3. Seed Bill of Materials (BoM)
    bom_data = [
        {
            "product_key": "Banner MMT_Banner 280gr Best",
            "nama": "BoM Banner MMT 280gr Standard",
            "items": [
                {"item_id": "INV-TEST-FLX280", "qty": 1.0},
                {"item_id": "INV-TEST-SOLCYN", "qty": 5.0}
            ]
        },
        {
            "product_key": "yasin soft cover_Art Paper 150",
            "nama": "BoM Yasin Softcover Standar",
            "items": [
                {"item_id": "INV-TEST-YSNBLK", "qty": 1.0}
            ]
        },
        {
            "product_key": "Kartu Nama_Ivory 260",
            "nama": "BoM Kartu Nama Ivory 260gr",
            "items": [
                {"item_id": "INV-TEST-IVR260", "qty": 0.1}
            ]
        }
    ]

    for bom_info in bom_data:
        prod_obj = products.get(bom_info["product_key"])
        if not prod_obj:
            continue
            
        bom_obj, created = BillOfMaterials.objects.update_or_create(
            product=prod_obj,
            defaults={"nama": bom_info["nama"]}
        )
        print(f"  - BoM: {bom_obj.nama} ({'Dibuat' if created else 'Diperbarui'})")
        
        # Clear existing items and rebuild to ensure exact match
        bom_obj.items.all().delete()
        for b_item in bom_info["items"]:
            inv_item = inventory_items.get(b_item["item_id"])
            if inv_item:
                BoMItem.objects.create(
                    bom=bom_obj,
                    inventory_item=inv_item,
                    qty_required_per_unit=b_item["qty"]
                )
                print(f"    * BoMItem: {inv_item.nama} - Qty: {b_item['qty']}")

    print("[SUCCESS] Seeding completed successfully!")

if __name__ == "__main__":
    seed()
