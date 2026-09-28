from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

# Neon's pooled endpoint (hostname containing "-pooler") runs PgBouncer in
# transaction mode, which doesn't play well with asyncpg's default
# client-side prepared-statement cache - it causes intermittent
# "prepared statement ... already exists" errors under concurrent use.
# Disabling that cache is the standard workaround; it's a no-op (harmless)
# against a direct, non-pooled Postgres connection.
connect_args = {}
if "+asyncpg" in settings.DATABASE_URL:
    connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    connect_args=connect_args,
    # Tests each pooled connection with a lightweight ping before handing it
    # out, transparently reconnecting if it's gone stale. Hosted Postgres
    # (Neon included) can close idle connections server-side; without this,
    # the next query after any idle period fails with a raw
    # "server closed the connection unexpectedly" instead of just working.
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()