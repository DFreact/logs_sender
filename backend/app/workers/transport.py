import dramatiq
from dramatiq.brokers.redis import RedisBroker
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.settings import Settings


def redis_client(settings: Settings) -> Redis:
    if not settings.redis_password_file:
        raise RuntimeError("BROKER_SECRET_REQUIRED")
    password = settings.redis_password_file.read_text().strip()
    if len(password) < 32:
        raise RuntimeError("BROKER_SECRET_INVALID")
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
    )


def broker_for(settings: Settings):
    # The DB owns retries and schedules. No default retry/result/prometheus middleware.
    return RedisBroker(client=redis_client(settings), middleware=[], namespace="eventhub")


def actor_for(broker, handler, queue="maintenance"):
    return dramatiq.actor(
        handler,
        broker=broker,
        actor_name={
            "maintenance": "execute_work",
            "ingestion": "normalize_work",
            "delivery": "deliver_work",
        }[queue],
        queue_name=queue,
    )
