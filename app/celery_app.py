import asyncio
import os

from celery import Celery


celery = Celery("universaldl")


def _ensure_windows_proactor_event_loop_policy():
	if os.name != "nt":
		return

	policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
	if policy_cls is None:
		return

	current_policy = asyncio.get_event_loop_policy()
	if isinstance(current_policy, policy_cls):
		return

	asyncio.set_event_loop_policy(policy_cls())


def init_celery(app):
	_ensure_windows_proactor_event_loop_policy()
	celery.config_from_object(app.config, namespace="CELERY")

	broker_url = app.config.get("CELERY_BROKER_URL")
	result_backend = app.config.get("CELERY_RESULT_BACKEND")
	if broker_url:
		celery.conf.broker_url = broker_url
	if result_backend:
		celery.conf.result_backend = result_backend

	from celery_beat import beat_schedule

	if not celery.conf.get("beat_schedule"):
		celery.conf.beat_schedule = beat_schedule

	download_queue = app.config.get("CELERY_DOWNLOAD_QUEUE") or "downloads"
	celery.conf.task_routes = {
		"tasks.download_media": {"queue": download_queue},
		"tasks.analyze_url": {"queue": download_queue},
		"tasks.cleanup_files": {"queue": download_queue},
		"tasks.subscription_poll": {"queue": download_queue},
		"tasks.extractor_health_check": {"queue": download_queue},
	}

	default_queue = app.config.get("CELERY_TASK_DEFAULT_QUEUE") or celery.conf.get("task_default_queue")
	if not default_queue:
		default_queue = download_queue
	celery.conf.task_default_queue = default_queue
	celery.conf.task_default_exchange = default_queue
	celery.conf.task_default_routing_key = default_queue

	existing_imports = set(celery.conf.get("imports") or ())
	existing_imports.add("app.downloader.tasks")
	celery.conf.imports = tuple(sorted(existing_imports))
	celery.autodiscover_tasks(["app.downloader"])

	if os.name == "nt":
		pool = celery.conf.get("worker_pool")
		if pool in (None, "", "prefork"):
			celery.conf.worker_pool = "threads"

	class ContextTask(celery.Task):
		def __call__(self, *args, **kwargs):
			with app.app_context():
				return self.run(*args, **kwargs)

	celery.Task = ContextTask
	return celery