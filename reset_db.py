import os
import MySQLdb
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

db_host = os.getenv('DB_HOST', '127.0.0.1')
db_user = os.getenv('DB_USER', 'root')
db_password = os.getenv('DB_PASSWORD', '')
try:
    db_port = int(os.getenv('DB_PORT', 3306))
except ValueError:
    db_port = 3306
db_name = os.getenv('DB_NAME', 'bintang_adv_db')

# Konek ke MySQL server (bukan ke database spesifik)
db = MySQLdb.connect(host=db_host, user=db_user, passwd=db_password, port=db_port)
cursor = db.cursor()

# Hapus database jika ada, dan buat ulang
cursor.execute(f"DROP DATABASE IF EXISTS {db_name};")
cursor.execute(f"CREATE DATABASE {db_name};")

print(f"[SUCCESS] Database {db_name} berhasil direset menjadi kosong!")

cursor.close()
db.close()
