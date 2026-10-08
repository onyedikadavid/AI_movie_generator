"""Synchronous SQLAlchemy engine/session, used by Celery workers and by the
run-control helpers (which the API calls through a thread)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Same asyncpg-vs-psycopg2 SSL param translation as alembic/env.py - needed if
# DATABASE_URL_OVERRIDE points at a hosted Postgres (Neon) that requires TLS.
sync_db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
sync_db_url = sync_db_url.replace("ssl=require", "sslmode=require")

# pool_pre_ping: the worker waits minutes at a time on GPU calls - without it
# the connection goes stale and the next commit fails with "server closed the
# connection unexpectedly". pool_recycle keeps Neon from dropping idle ones.
engine = create_engine(
    sync_db_url, pool_pre_ping=True, pool_recycle=300, pool_size=5, max_overflow=5,
    # TCP keepalives so NAT / hosted-Postgres proxies don't silently drop a connection during a long GPU wait.
    connect_args={"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5},
)
SessionLocal = sessionmaker(bind=engine)
