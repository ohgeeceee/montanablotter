import os
from urllib.parse import urlparse
from redis import Redis
from rq import Queue

REDIS_URL = os.getenv("MB_REDIS_URL") or os.getenv("REDIS_URL")
REDIS_HOST = os.getenv("MB_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("MB_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("MB_REDIS_DB", "0"))


def _build_redis_connection() -> Redis:
    """Build the canonical Redis connection used by the ingestion stack.

    IMPORTANT: ``decode_responses`` must match what the rq CLI workers use
    (default ``True``). The earlier ``decode_responses=False`` setting split
    queue writes (from email_worker / app enqueues) onto an
    ``:intermediate`` key that the CLI workers could not see, and caused
    the workers to crash with ``UnicodeDecodeError: 'utf-8' codec can't
    decode byte 0x9c`` when they did manage to dequeue.  Keep this in sync
    with any rq CLI invocation (the systemd unit ``montanablotter-rq-*@``
    templates do not pass an explicit connection, so they use rq's default
    of ``decode_responses=True``).  See the ingestion audit (2026-06-13).
    """
    if REDIS_URL:
        # ``redis://[:pwd@]host:port/db`` — let redis-py pick kwargs.
        return Redis.from_url(REDIS_URL, decode_responses=True)
    return Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True,
    )


redis_conn = _build_redis_connection()

ingestion_q = Queue(
    "ingestion",
    connection=redis_conn,
    default_timeout=15 * 60,  # 15 min
)

parsing_q = Queue(
    "parsing",
    connection=redis_conn,
    default_timeout=30 * 60,  # 30 min
)

publishing_q = Queue(
    "publishing",
    connection=redis_conn,
    default_timeout=30 * 60,  # 30 min
)