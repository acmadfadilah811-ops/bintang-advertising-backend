from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hr"
    verbose_name = "HR & Kepegawaian"

    def ready(self):
        import sys
        if 'runserver' in sys.argv:
            from django.core.management import call_command
            try:
                call_command('migrate', 'hr', interactive=False)
            except Exception as e:
                print("Auto-migration error for hr app:", e)
