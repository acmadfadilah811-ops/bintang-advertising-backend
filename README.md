# Bintang Advertising Backend

## Deploy
1. Isi environment production (`SECRET_KEY`, `ALLOWED_HOSTS`, `REDIS_URL`, database, email, `NUM_PROXIES`).
2. Jalankan `python manage.py migrate`.
3. Jalankan `python manage.py check --deploy`.
4. Jalankan test sebelum merilis.

Migration audit terkini: `0077_audit_security_integrity.py`.
