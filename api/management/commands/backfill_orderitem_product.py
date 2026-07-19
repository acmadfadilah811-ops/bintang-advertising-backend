"""Isi OrderItem.product dari teks bebas `jenis_produk`.

Dipakai sekali setelah migrasi penambahan FK OrderItem->Product, tapi aman
dijalankan ulang (idempoten): baris yang sudah punya product dilewati.

Pencocokan bertahap: exact -> iexact -> icontains (hanya bila hasilnya tunggal,
supaya tidak salah tebak). Yang tidak cocok dibiarkan NULL dan dilaporkan.
"""
from django.core.management.base import BaseCommand
from django.db.models.functions import Lower

from api.models import OrderItem
from api.product_models import Product


class Command(BaseCommand):
    help = "Cocokkan OrderItem.jenis_produk ke master Product dan isi FK product."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Hanya tampilkan hasil pencocokan tanpa menyimpan.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Peta nama produk (lowercase) -> id. Nama duplikat dibuang dari peta
        # exact/iexact karena ambigu.
        pairs = list(Product.objects.annotate(n=Lower('nama')).values_list('n', 'id'))
        seen = {}
        ambiguous = set()
        for nama_lower, pid in pairs:
            if nama_lower in seen:
                ambiguous.add(nama_lower)
            else:
                seen[nama_lower] = pid
        for a in ambiguous:
            seen.pop(a, None)

        items = OrderItem.objects.filter(product__isnull=True).only('id', 'jenis_produk')
        total = items.count()
        matched = 0
        unmatched_samples = []
        to_update = []

        for item in items.iterator():
            teks = (item.jenis_produk or '').strip()
            if not teks:
                continue
            key = teks.lower()
            pid = seen.get(key)

            if pid is None:
                # Fallback: substring, hanya bila persis satu kandidat.
                kandidat = [v for k, v in seen.items() if key and (key in k or k in key)]
                if len(kandidat) == 1:
                    pid = kandidat[0]

            if pid is None:
                if len(unmatched_samples) < 15:
                    unmatched_samples.append(teks)
                continue

            item.product_id = pid
            to_update.append(item)
            matched += 1

        if not dry_run and to_update:
            OrderItem.objects.bulk_update(to_update, ['product'], batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f"Item tanpa product: {total} | cocok: {matched} | tidak cocok: {total - matched}"
        ))
        if ambiguous:
            self.stdout.write(self.style.WARNING(
                f"{len(ambiguous)} nama produk duplikat dilewati (ambigu): "
                + ', '.join(sorted(ambiguous)[:10])
            ))
        if unmatched_samples:
            self.stdout.write("Contoh jenis_produk yang tidak cocok:")
            for s in unmatched_samples:
                self.stdout.write(f"  - {s}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — tidak ada perubahan disimpan."))
