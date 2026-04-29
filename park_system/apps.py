from django.apps import AppConfig


class ParkSystemConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'park_system'

    def ready(self):
        # create schedules when Django starts
        try:
            from django.db import connection
            if connection.vendor:  # DB is available
                from .schedules import create_schedules
                create_schedules()
        except Exception:
            pass  # silently skip if DB isn't ready yet
