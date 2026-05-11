import os
from redis import Redis
from rq import Queue

REDIS_HOST = os.getenv("MB_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("MB_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("MB_REDIS_DB", "0"))

redis_conn = Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=False,
)

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