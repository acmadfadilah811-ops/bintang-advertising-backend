import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import pos_settings, stock_fifo, uom
from .models import Contact, SaldoKasHarian
from .pos_models import POSSale, POSSaleItem
from .product_models import Product, ProductVariant, ProductStockMovement

MONEY = Decimal('0.01')
QTY = Decimal('0.01')

def money(value):
    try:
        return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({'error': 'Nilai uang tidak valid.'})

def percentage(value, field):
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({'error': f'{field} tidak valid.'})
    if result < 0 or result > 100:
        raise ValidationError({'error': f'{field} harus antara 0 dan 100.'})
    return result

def _nomor():
    now = timezone.now()
    return f"POS-{now.strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:6].upper()}"

def _server_price(product, variant):
    if variant and variant.harga_jual_toko is not None:
        return money(variant.harga_jual_toko)
    return money(product.harga_jual_toko)

def create_sale(*, user, data):
    items = data.get('items') or []
    if not items:
        raise ValidationError({'error': 'Tidak ada item dalam keranjang.'})
    status_val = data.get('status', 'paid')
    if status_val not in ('paid', 'hold'):
        raise ValidationError({'error': 'Status transaksi tidak valid.'})
    if status_val == 'hold' and pos_settings.blokir_tahan_pesanan():
        raise ValidationError({'error': 'Menahan pesanan dinonaktifkan.'})

    discount_pct = percentage(data.get('diskon_persen', 0), 'diskon_persen')
    tax_pct = percentage(data.get('pajak_persen', 0), 'pajak_persen')
    paid = money(data.get('dibayar', 0))

    with transaction.atomic():
        shift = (SaldoKasHarian.objects.select_for_update()
                 .filter(kasir=user, kas_akhir__isnull=True, waktu_tutup__isnull=True)
                 .order_by('-id').first())
        if pos_settings.wajib_shift_aktif() and shift is None:
            raise ValidationError({'error': 'Buka shift Anda sendiri sebelum transaksi.'})

        prepared = []
        subtotal = Decimal('0')
        requested = {}
        for raw in items:
            product_id = raw.get('product_id')
            if not product_id:
                raise ValidationError({'error': 'Item non-katalog tidak dapat ditransaksikan langsung di POS.'})
            product = Product.objects.select_for_update().filter(pk=product_id, is_active=True).first()
            if not product:
                raise ValidationError({'error': 'Produk tidak ditemukan atau tidak aktif.'})
            variant = None
            if raw.get('variant_id'):
                variant = ProductVariant.objects.select_for_update().filter(
                    pk=raw['variant_id'], product=product
                ).first()
                if not variant:
                    raise ValidationError({'error': f'Varian untuk {product.nama} tidak valid.'})
            try:
                input_qty = Decimal(str(raw.get('qty', 1)))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError({'error': f'Qty {product.nama} tidak valid.'})
            if input_qty <= 0:
                raise ValidationError({'error': f'Qty {product.nama} harus lebih dari nol.'})
            conversion = uom.resolve(product, raw.get('uom_kode'), input_qty, None, variant)
            qty_base = Decimal(str(conversion['qty_dasar'])).quantize(QTY)
            price_base = _server_price(product, variant)
            line_total = money(price_base * qty_base)
            key = (product.id, variant.id if variant else None)
            requested[key] = requested.get(key, Decimal('0')) + qty_base
            prepared.append((raw, product, variant, conversion, qty_base, price_base, line_total))
            subtotal += line_total

        if status_val == 'paid' and pos_settings.pos_mengurangi_stok():
            for raw, product, variant, conversion, qty_base, price_base, line_total in prepared:
                owner = variant or product
                if product.lacak_inventori and requested[(product.id, variant.id if variant else None)] > Decimal(str(owner.qty_stok or 0)):
                    raise ValidationError({'error': f"Stok '{owner}' tidak mencukupi."})

        discount = money(subtotal * discount_pct / Decimal('100'))
        tax = money((subtotal - discount) * tax_pct / Decimal('100'))
        total = money(subtotal - discount + tax)
        if status_val == 'paid' and paid < total:
            raise ValidationError({'error': 'Jumlah pembayaran belum mencukupi total server.'})
        client_total = data.get('total')
        if client_total not in (None, '') and money(client_total) != total:
            raise ValidationError({'error': 'Total transaksi berubah. Muat ulang harga produk lalu coba lagi.', 'server_total': total})

        customer = None
        if data.get('pelanggan'):
            customer = Contact.objects.filter(pk=data['pelanggan']).first()
            if customer is None:
                raise ValidationError({'error': 'Pelanggan tidak valid.'})

        sale = POSSale.objects.create(
            nomor=_nomor(), kasir=user, pelanggan=customer, shift=shift,
            subtotal=money(subtotal), diskon=discount, pajak=tax, total=total,
            metode_bayar=str(data.get('metode_bayar') or 'Cash')[:50], dibayar=paid,
            kembalian=money(max(Decimal('0'), paid-total)),
            catatan=str(data.get('catatan') or '')[:2000], status=status_val,
        )
        now = timezone.localdate()
        for raw, product, variant, conversion, qty_base, price_base, line_total in prepared:
            POSSaleItem.objects.create(
                sale=sale, product=product, variant=variant, nama_snapshot=product.nama,
                harga_snapshot=price_base, qty=qty_base, subtotal=line_total,
                catatan=str(raw.get('catatan') or '')[:2000],
                uom_kode=conversion['uom_kode'], uom_konverter=conversion['uom_konverter'],
                uom_qty=conversion['uom_qty'],
                uom_harga=(money(price_base * conversion['uom_konverter']) if conversion['uom_kode'] else None),
            )
            if status_val == 'paid' and pos_settings.pos_mengurangi_stok() and product.lacak_inventori:
                owner = variant or product
                start = owner.qty_stok
                owner.qty_stok = start - qty_base
                owner.save(update_fields=['qty_stok'])
                movement = ProductStockMovement.objects.create(
                    product=product, variant=variant, user=user, tipe='penjualan', qty=qty_base,
                    stok_awal=start, stok_akhir=owner.qty_stok,
                    catatan=f'Penjualan POS {sale.nomor}', tanggal=now,
                )
                stock_fifo.consume_layers(product, variant, qty_base, movement=movement)
        return sale

def void_sale(*, sale_id, user):
    with transaction.atomic():
        sale = POSSale.objects.select_for_update().prefetch_related('items').get(pk=sale_id)
        if sale.status == 'void':
            raise ValidationError({'error': 'Transaksi sudah dibatalkan sebelumnya.'})
        if sale.status == 'paid' and pos_settings.pos_mengurangi_stok():
            for item in sale.items.select_related('product', 'variant'):
                if not item.product or not item.product.lacak_inventori:
                    continue
                product = Product.objects.select_for_update().get(pk=item.product_id)
                variant = (ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                           if item.variant_id else None)
                owner = variant or product
                start = owner.qty_stok
                owner.qty_stok = start + item.qty
                owner.save(update_fields=['qty_stok'])
                original = (ProductStockMovement.objects.filter(
                    product=product, variant=variant, tipe='penjualan',
                    catatan=f'Penjualan POS {sale.nomor}'
                ).order_by('id').first())
                restored_hpp = Decimal('0')
                if original:
                    for consumption in original.layer_consumptions.select_related('layer').all():
                        restored_hpp += consumption.qty * consumption.harga_beli
                        if consumption.layer_id:
                            layer = consumption.layer
                            layer.sisa_qty += consumption.qty
                            layer.save(update_fields=['sisa_qty'])
                ProductStockMovement.objects.create(
                    product=product, variant=variant, user=user, tipe='pengembalian', qty=item.qty,
                    stok_awal=start, stok_akhir=owner.qty_stok, hpp_total=restored_hpp,
                    catatan=f'Pembatalan POS (Void) {sale.nomor}', tanggal=timezone.localdate(),
                )
        sale.status = 'void'
        sale.save(update_fields=['status'])
        return sale
