import ssl
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from celery import Celery
from app.core.config import settings


def _ensure_ssl_cert_reqs(redis_url: str) -> str:
    """
    Celery's redis result backend refuses to start against a rediss:// (TLS)
    URL unless ssl_cert_reqs is explicitly present in the query string
    (celery.backends.redis.E_REDIS_SSL_CERT_REQS_MISSING_INVALID). Hosted
    Redis providers like Upstash hand you a plain rediss:// URL with no such
    parameter, so add it here rather than relying on everyone remembering to
    hand-edit REDIS_URL_OVERRIDE themselves.
    """
    if not redis_url.startswith("rediss://"):
        return redis_url

    parts = urlsplit(redis_url)
    query = dict(parse_qsl(parts.query))
    query.setdefault("ssl_cert_reqs", "CERT_REQUIRED")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


REDIS_URL = _ensure_ssl_cert_reqs(settings.REDIS_URL)
_is_tls = REDIS_URL.startswith("rediss://")

celery_app = Celery(
    "worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    # CRITICAL: without this, the worker process never imports
    # pipeline_tasks.py at all, so its @celery_app.task-decorated functions
    # never register with THIS process. The API process imports
    # pipeline_tasks.py fine (via projects.py), but the worker is a
    # completely separate Python process that only imports what's listed
    # here - hence "Received unregistered task ... KeyError" even though
    # the task is clearly defined and dispatched successfully.
    include=["app.tasks.pipeline_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Explicit SSL options for the broker side too (the query-param fix above
    # covers the result backend; the broker connection needs this separately).
    # If this ever fails with a certificate verification error against a
    # provider using a non-standard CA, CERT_NONE skips verification as a
    # last resort (less secure - only use it if CERT_REQUIRED genuinely
    # doesn't work for your provider).
    broker_use_ssl={"ssl_cert_reqs": ssl.CERT_REQUIRED} if _is_tls else None,
    redis_backend_use_ssl={"ssl_cert_reqs": ssl.CERT_REQUIRED} if _is_tls else None,
    # A hosted Redis proxy (Upstash, Redis Cloud) will drop idle TCP
    # connections from time to time - that's normal, not a sign anything is
    # wrong. Without these settings, Celery/redis-py can raise an unhandled
    # TimeoutError instead of transparently reconnecting, which kills the
    # whole worker process. These make it actually recover instead of crashing.
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=0,  # retry forever rather than giving up
    broker_transport_options={
        "socket_keepalive": True,
        "socket_timeout": 30,
        "socket_connect_timeout": 30,
        "retry_on_timeout": True,
        "health_check_interval": 25,  # ping before Upstash's own idle cutoff
    },
    redis_backend_transport_options={
        "socket_keepalive": True,
        "socket_timeout": 30,
        "retry_on_timeout": True,
        "health_check_interval": 25,
    },
)
