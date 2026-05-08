from celery.schedules import crontab


beat_schedule = {
    "cleanup-expired-files": {
        "task": "tasks.cleanup_files",
        "schedule": 900.0,
    },
    "subscription-poll": {
        "task": "tasks.subscription_poll",
        "schedule": 1800.0,
    },
    "extractor-health-check": {
        "task": "tasks.extractor_health_check",
        "schedule": 1800.0,
    },
    "calculate-success-rates": {
        "task": "tasks.calculate_success_rates",
        "schedule": crontab(minute=0),
    },
    "purge-old-url-logs": {
        "task": "tasks.purge_old_url_logs",
        "schedule": crontab(hour=3, minute=0),
    },
}
