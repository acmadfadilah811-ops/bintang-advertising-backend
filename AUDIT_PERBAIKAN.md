# Implementasi Temuan Audit — 21 Juli 2026

Paket ini menerapkan perbaikan prioritas audit, terutama enforcement server-side.

## Tuntas
- POS: harga/total dihitung ulang dari database, stok dikunci, shift wajib milik kasir, nomor bebas race, verb REST dikunci, void hanya owner/manager, FIFO void dipulihkan dari konsumsi asli.
- Autentikasi: throttle login/reset/PassKey, lockout, reset-token terikat request, maksimal lima percobaan OTP, respons anti-enumerasi, IP proxy tervalidasi.
- Otorisasi: pengaturan sensitif, POS, finance, kontak, customer, job, user role, order-item, shift, dan dashboard diperketat.
- Stok: posting dokumen memakai row lock, kegagalan stok me-rollback, stok varian konsisten, harga beli rata-rata tertimbang.
- Keuangan: uang shift memakai Decimal, transaksi kas ditautkan ke shift, jurnal payroll ikut rollback, pembayaran order punya idempotency key.
- Infrastruktur: migration tunggal `0077`, Redis/email/hosts fail-closed di production, STORAGES modern, proxy count, security header, container non-root dan healthcheck.
- Frontend: route default-deny, path kasir benar, shift global dihapus, pembulatan Rupiah, split bill/keranjang diperbaiki, guard double-submit, pagination eksplisit, secret bundle dihapus, Sentry sampling diturunkan.

## Verifikasi
- Semua file Python lolos AST dan compileall.
- Semua file JS/JSX lolos Babel parser.
- Graph modul frontend berhasil dibundle oleh esbuild.
- Graph migration memiliki satu leaf: `0077_audit_security_integrity`.

## Deploy
`python manage.py migrate && python manage.py check --deploy`

## Koreksi verifikasi kedua
- Delapan indeks skalabilitas lengkap dan urutan descending dipulihkan dalam model serta migration 0077.
- Default waktu order manual memakai `toLocalDateTimeInput()` sehingga tidak lagi bergeser UTC.
- Guard legacy 1.000 baris dipulihkan. POS mengirim pagination eksplisit, membuka `results`, dan menampilkan kegagalan pengambilan data kepada pengguna.
