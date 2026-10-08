"""
Celery application factory.

Why Redis as broker?
- Simple to set up with Docker Compose (one extra service, no config files)
- Good enough throughput for our use case (one document at a time per worker)
- In production you'd switch to RabbitMQ or SQS for durability guarantees

The result backend (redis:6379/1) uses a separate DB index from the broker
so they don't interfere with each other.
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "cargo_copilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],  # auto-discover tasks in this module
)

celery_app.conf.update(
    # Serialize tasks as JSON (not pickle) for security
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Tasks that exceed this time are terminated
    task_time_limit=600,           # 10 minutes hard limit
    task_soft_time_limit=540,      # 9 minutes soft limit (raises SoftTimeLimitExceeded)

    # Retry failed tasks with exponential backoff
    task_acks_late=True,           # acknowledge only after task completes (safer)
    task_reject_on_worker_lost=True,

    # Timezone
    timezone="UTC",
    enable_utc=True,
)
