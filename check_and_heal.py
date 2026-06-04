"""
============================================================
 System Health & Auto-Healing Bot
 Brandy CRM Backend — Diagnostic Tool
============================================================

Cara Pakai:
  Pemeriksaan cepat (tanpa unit test):
      uv run python check_and_heal.py

  Pemeriksaan lengkap (dengan unit test):
      uv run python check_and_heal.py --run-tests

Log disimpan di: health_bot.log
============================================================
"""

import os
import sys
import time
import django
import shutil
import logging

# ---------------------------------------------------------------------------
# Setup Django Environment
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

try:
    django.setup()
except Exception as e:
    print(f"[FATAL] Gagal menginisialisasi Django: {e}")
    sys.exit(1)

from django.db import connection
from django.core.cache import cache
from django.core.management import call_command
from django.test.client import Client
from api.models import CustomUser, Order, JobBoard, InventoryItem

# ---------------------------------------------------------------------------
# Logger Setup (ke stdout DAN file log)
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_bot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("HealthBot")

# ---------------------------------------------------------------------------
# Konstanta Threshold
# ---------------------------------------------------------------------------
DISK_WARN_THRESHOLD_PERCENT = 10.0   # Peringatkan jika disk tersisa < 10%
API_SLOW_THRESHOLD_MS       = 2000   # Tandai sebagai lambat jika > 2000ms
ENV_VARS_WAJIB = [
    "SECRET_KEY",
    "DB_NAME",
    "DB_USER",
    "DB_HOST",
]
ENV_VARS_OPSIONAL = [
    "KOBOI_API_KEY",      # AI WhatsApp bot
    "FONNTE_API_KEY",     # WhatsApp gateway
    "SENTRY_DSN",         # Error monitoring
    "REDIS_URL",          # Cache server
]


# ===========================================================================
class SystemHealthBot:
# ===========================================================================

    def __init__(self):
        self.health_status = {
            "database"        : "UNKNOWN",
            "migrations"      : "UNKNOWN",
            "cache"           : "UNKNOWN",
            "env_variables"   : "UNKNOWN",
            "disk"            : "UNKNOWN",
            "api_routing"     : "UNKNOWN",
            "business_data"   : "UNKNOWN",
            "unit_tests"      : "UNKNOWN",
        }
        self.healed_actions = []
        self.warnings       = []

    # -----------------------------------------------------------------------
    def _header(self, title):
        logger.info("=" * 60)
        logger.info(f"  Checking: {title}")
        logger.info("=" * 60)

    # -----------------------------------------------------------------------
    # 1. KONEKSI DATABASE
    # -----------------------------------------------------------------------
    def check_database(self):
        self._header("Koneksi Database MySQL")
        try:
            connection.ensure_connection()
            user_count  = CustomUser.objects.count()
            order_count = Order.objects.count()
            job_count   = JobBoard.objects.count()
            logger.info(f"[OK] Database terhubung dan responsif.")
            logger.info(f"     -> Akun pengguna : {user_count}")
            logger.info(f"     -> Total pesanan : {order_count}")
            logger.info(f"     -> Total job     : {job_count}")
            self.health_status["database"] = "HEALTHY"
        except Exception as e:
            logger.error(f"[ERROR] Koneksi database gagal: {e}")
            self.health_status["database"] = "UNHEALTHY"

    # -----------------------------------------------------------------------
    # 2. STATUS MIGRASI (AUTO-HEALING)
    # -----------------------------------------------------------------------
    def check_migrations(self):
        self._header("Status Migrasi Database")
        if self.health_status["database"] != "HEALTHY":
            logger.warning("[SKIP] Database tidak sehat, pengecekan migrasi dilewati.")
            self.health_status["migrations"] = "SKIPPED"
            return

        try:
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())

            if plan:
                pending = [f"{m[0].app_label}.{m[0].name}" for m in plan]
                logger.warning(f"[WARNING] {len(plan)} migrasi belum diterapkan:")
                for name in pending:
                    logger.warning(f"          -> {name}")
                self.health_status["migrations"] = f"WARNING ({len(plan)} PENDING)"

                # Auto-healing: terapkan migrasi
                logger.info("[HEALING] Menjalankan 'migrate' secara otomatis...")
                try:
                    call_command("migrate", interactive=False, verbosity=0)
                    logger.info("[OK] Migrasi berhasil diterapkan otomatis!")
                    self.healed_actions.append(f"Diterapkan {len(plan)} migrasi database yang tertunda.")
                    self.health_status["migrations"] = "HEALTHY (HEALED)"
                except Exception as err:
                    logger.error(f"[ERROR] Auto-migrate gagal: {err}")
                    self.health_status["migrations"] = "UNHEALTHY"
            else:
                logger.info("[OK] Semua migrasi sudah ter-update. Skema database bersih.")
                self.health_status["migrations"] = "HEALTHY"

        except Exception as e:
            logger.error(f"[ERROR] Gagal memeriksa status migrasi: {e}")
            self.health_status["migrations"] = "UNHEALTHY"

    # -----------------------------------------------------------------------
    # 3. SISTEM CACHE & REDIS (AUTO-HEALING)
    # -----------------------------------------------------------------------
    def check_cache(self):
        self._header("Sistem Cache & Redis")
        try:
            test_key = "health_bot_ping"
            cache.set(test_key, "pong", timeout=10)
            value = cache.get(test_key)
            cache.delete(test_key)

            if value == "pong":
                # Cek backend yang digunakan
                backend = type(cache).__module__
                logger.info(f"[OK] Cache responsif. Backend: {backend}")
                self.health_status["cache"] = "HEALTHY"
            else:
                logger.error("[ERROR] Cache mengembalikan nilai yang tidak valid.")
                self.health_status["cache"] = "UNHEALTHY"

        except Exception as e:
            logger.error(f"[ERROR] Cache tidak dapat dihubungi: {e}")
            self.health_status["cache"] = "UNHEALTHY"
            try:
                cache.clear()
                logger.info("[HEALING] Cache berhasil dibersihkan.")
                self.healed_actions.append("Cache dibersihkan akibat error koneksi.")
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # 4. VARIABEL ENVIRONMENT
    # -----------------------------------------------------------------------
    def check_env_variables(self):
        self._header("Variabel Environment (.env)")
        missing_wajib    = []
        missing_opsional = []

        for key in ENV_VARS_WAJIB:
            val = os.getenv(key, "")
            if not val:
                missing_wajib.append(key)
            else:
                # Tampilkan nilai (sensor sebagian untuk keamanan)
                display = val[:4] + "****" if len(val) > 4 else "****"
                logger.info(f"[OK] {key:<25} = {display}")

        for key in ENV_VARS_OPSIONAL:
            val = os.getenv(key, "")
            if not val:
                missing_opsional.append(key)
                logger.warning(f"[WARN] {key:<25} = (tidak diset — fitur terkait mungkin tidak aktif)")
            else:
                display = val[:4] + "****" if len(val) > 4 else "****"
                logger.info(f"[OK] {key:<25} = {display}")

        if missing_wajib:
            logger.error(f"[ERROR] Variabel WAJIB tidak ditemukan: {', '.join(missing_wajib)}")
            self.health_status["env_variables"] = f"UNHEALTHY (MISSING: {', '.join(missing_wajib)})"
        elif missing_opsional:
            self.warnings.append(f"Variabel opsional tidak diset: {', '.join(missing_opsional)}")
            logger.info("[OK] Semua variabel wajib tersedia.")
            self.health_status["env_variables"] = "HEALTHY"
        else:
            logger.info("[OK] Semua variabel environment tersedia.")
            self.health_status["env_variables"] = "HEALTHY"

    # -----------------------------------------------------------------------
    # 5. KAPASITAS DISK
    # -----------------------------------------------------------------------
    def check_disk_space(self):
        self._header("Kapasitas Disk Storage")
        try:
            total, used, free = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
            free_gb      = free  / (2 ** 30)
            total_gb     = total / (2 ** 30)
            used_gb      = used  / (2 ** 30)
            free_percent = (free / total) * 100

            logger.info(f"     Total : {total_gb:.1f} GB")
            logger.info(f"     Dipakai : {used_gb:.1f} GB")
            logger.info(f"     Tersisa : {free_gb:.2f} GB ({free_percent:.1f}%)")

            if free_percent < DISK_WARN_THRESHOLD_PERCENT:
                logger.warning(f"[WARNING] Kapasitas disk KRITIS — tersisa {free_percent:.1f}%!")
                self.health_status["disk"] = "WARNING (LOW SPACE)"
            else:
                logger.info("[OK] Kapasitas disk dalam kondisi aman.")
                self.health_status["disk"] = "HEALTHY"

        except Exception as e:
            logger.error(f"[ERROR] Gagal memeriksa kapasitas disk: {e}")
            self.health_status["disk"] = "UNHEALTHY"

    # -----------------------------------------------------------------------
    # 6. UJI ROUTING API ENDPOINT (Dengan Pengukuran Waktu Respons)
    # -----------------------------------------------------------------------
    def check_api_routing(self):
        self._header("Uji Routing API Endpoints")
        if self.health_status["database"] != "HEALTHY":
            logger.warning("[SKIP] Database tidak sehat, pengecekan routing dilewati.")
            self.health_status["api_routing"] = "SKIPPED"
            return

        client = Client()

        # Endpoint publik yang dapat dicek tanpa autentikasi
        endpoints = [
            ("/api/schema/",           200, "OpenAPI Schema Docs"),
            ("/api/auth/login/",       405, "JWT Login Endpoint"),    # 405 = GET tidak diizinkan, hanya POST
            ("/api/auth/refresh/",     405, "JWT Refresh Endpoint"),  # 405 = GET tidak diizinkan, hanya POST
            ("/api/orders/",           401, "Orders API (Unauth)"),   # 401 = butuh login
            ("/api/jobs/",             401, "Jobs API (Unauth)"),
            ("/api/hr/absensi/",       401, "Absensi API (Unauth)"),
            ("/api/inventory/",        401, "Inventory API (Unauth)"),
            ("/api/dashboard/",        401, "Dashboard API (Unauth)"),
        ]

        failed   = False
        slowness = False
        for url, expected_status, name in endpoints:
            try:
                t_start  = time.monotonic()
                response = client.get(url)
                elapsed_ms = (time.monotonic() - t_start) * 1000

                status_ok = response.status_code == expected_status
                speed_tag = f"{elapsed_ms:.0f}ms"

                if elapsed_ms > API_SLOW_THRESHOLD_MS:
                    speed_tag += " [LAMBAT]"
                    slowness = True
                    self.warnings.append(f"Endpoint {url} lambat: {elapsed_ms:.0f}ms")

                if status_ok:
                    logger.info(f"[OK]  {name:<32} HTTP {response.status_code}  ({speed_tag})")
                else:
                    logger.warning(
                        f"[WARN] {name:<32} HTTP {response.status_code} "
                        f"(diharapkan {expected_status})  ({speed_tag})"
                    )
                    failed = True

            except Exception as e:
                logger.error(f"[ERROR] {name} ({url}) error: {e}")
                failed = True

        if failed:
            self.health_status["api_routing"] = "UNHEALTHY"
        elif slowness:
            self.health_status["api_routing"] = "HEALTHY (SLOW RESPONSE)"
        else:
            self.health_status["api_routing"] = "HEALTHY"

    # -----------------------------------------------------------------------
    # 7. INTEGRITAS DATA BISNIS
    # -----------------------------------------------------------------------
    def check_business_data(self):
        self._header("Integritas Data Bisnis")
        if self.health_status["database"] != "HEALTHY":
            logger.warning("[SKIP] Database tidak sehat, pengecekan data bisnis dilewati.")
            self.health_status["business_data"] = "SKIPPED"
            return

        issues = []
        try:
            # Cek order tanpa item
            from django.db.models import Count
            orders_kosong = Order.objects.annotate(n=Count("items")).filter(n=0).count()
            if orders_kosong > 0:
                issues.append(f"{orders_kosong} pesanan tidak memiliki item (order kosong).")
                logger.warning(f"[WARN] {orders_kosong} pesanan ditemukan tanpa OrderItem.")
            else:
                logger.info("[OK] Tidak ada pesanan kosong tanpa item.")

            # Cek job orphan (tanpa order_item)
            jobs_orphan = JobBoard.objects.filter(order_item__isnull=True).count()
            if jobs_orphan > 0:
                issues.append(f"{jobs_orphan} job board tidak tertaut ke pesanan.")
                logger.warning(f"[WARN] {jobs_orphan} job tanpa order_item ditemukan.")
            else:
                logger.info("[OK] Tidak ada job board yang orphan.")

            # Cek stok kritis
            from django.db.models import F
            stok_kritis = InventoryItem.objects.filter(stok__lt=F("min_stok")).count()
            if stok_kritis > 0:
                msg = f"{stok_kritis} bahan baku stoknya di bawah minimum."
                issues.append(msg)
                logger.warning(f"[WARN] {msg}")
            else:
                logger.info("[OK] Semua stok bahan baku di atas batas minimum.")

            # Cek akun owner
            owner_count = CustomUser.objects.filter(role="owner", is_active=True).count()
            if owner_count == 0:
                issues.append("Tidak ada akun aktif dengan role Owner!")
                logger.error("[ERROR] Tidak ditemukan akun Owner aktif di sistem.")
            else:
                logger.info(f"[OK] Akun Owner aktif ditemukan: {owner_count} akun.")

            if issues:
                self.warnings.extend(issues)
                self.health_status["business_data"] = f"WARNING ({len(issues)} issues)"
            else:
                self.health_status["business_data"] = "HEALTHY"

        except Exception as e:
            logger.error(f"[ERROR] Gagal memeriksa integritas data bisnis: {e}")
            self.health_status["business_data"] = "UNHEALTHY"

    # -----------------------------------------------------------------------
    # 8. UNIT & INTEGRATION TESTS (Opsional)
    # -----------------------------------------------------------------------
    def run_unit_tests(self):
        self._header("Menjalankan Unit & Integration Tests")
        if self.health_status["database"] != "HEALTHY":
            logger.warning("[SKIP] Database tidak sehat, unit tests dilewati.")
            self.health_status["unit_tests"] = "SKIPPED"
            return
        try:
            from django.test.runner import DiscoverRunner
            test_runner = DiscoverRunner(verbosity=1, interactive=False, keepdb=True)
            failures = test_runner.run_tests(["api.tests"])
            if failures == 0:
                logger.info("[OK] Semua Unit & Integration Tests lulus (0 kegagalan).")
                self.health_status["unit_tests"] = "HEALTHY"
            else:
                logger.error(f"[ERROR] {failures} kegagalan ditemukan pada unit tests.")
                self.health_status["unit_tests"] = f"UNHEALTHY ({failures} FAILURES)"
        except Exception as e:
            logger.error(f"[ERROR] Gagal menjalankan test runner: {e}")
            self.health_status["unit_tests"] = "UNHEALTHY"

    # -----------------------------------------------------------------------
    # MAIN RUNNER
    # -----------------------------------------------------------------------
    def run_all(self):
        run_tests = "--run-tests" in sys.argv

        logger.info("=" * 60)
        logger.info("  BRANDY CRM — SYSTEM HEALTH & AUTO-HEALING BOT")
        logger.info("=" * 60)

        self.check_database()
        self.check_migrations()
        self.check_cache()
        self.check_env_variables()
        self.check_disk_space()
        self.check_api_routing()
        self.check_business_data()

        if run_tests:
            self.run_unit_tests()
        else:
            self.health_status["unit_tests"] = "SKIPPED (gunakan --run-tests)"

        # -------------------------------------------------------------------
        # LAPORAN AKHIR
        # -------------------------------------------------------------------
        logger.info("\n" + "=" * 60)
        logger.info("  LAPORAN AKHIR KESEHATAN SISTEM")
        logger.info("=" * 60)

        overall_healthy = True
        for component, status in self.health_status.items():
            label = component.replace("_", " ").title()
            logger.info(f"  {label:<22} : [{status}]")
            if (
                "HEALTHY" not in status
                and "SKIPPED" not in status
                and status != "UNKNOWN"
            ):
                overall_healthy = False

        if self.healed_actions:
            logger.info("-" * 60)
            logger.info("  AUTO-HEALING YANG DILAKUKAN:")
            for act in self.healed_actions:
                logger.info(f"  * {act}")

        if self.warnings:
            logger.info("-" * 60)
            logger.info("  PERINGATAN (Tidak Kritis):")
            for w in self.warnings:
                logger.info(f"  ! {w}")

        logger.info("=" * 60)
        if overall_healthy:
            logger.info("  KESIMPULAN: Sistem sehat dan siap digunakan.")
        else:
            logger.warning("  KESIMPULAN: Ada masalah yang perlu ditangani. Periksa log.")
        logger.info(f"  Log lengkap tersimpan di: {LOG_FILE}")
        logger.info("=" * 60)

        return 0 if overall_healthy else 1


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot = SystemHealthBot()
    exit_code = bot.run_all()
    sys.exit(exit_code)
