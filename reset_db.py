import MySQLdb

# Konek ke MySQL server (bukan ke database spesifik)
db = MySQLdb.connect(host="127.0.0.1", user="root", passwd="", port=3306)
cursor = db.cursor()

# Hapus database jika ada, dan buat ulang
cursor.execute("DROP DATABASE IF EXISTS bintang_adv_db;")
cursor.execute("CREATE DATABASE bintang_adv_db;")

print("[SUCCESS] Database bintang_adv_db berhasil direset menjadi kosong!")

cursor.close()
db.close()
