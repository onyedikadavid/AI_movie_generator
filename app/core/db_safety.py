"""
Small helpers that keep a long-running worker alive when the hosted database drops a connection that sat idle
during a multi-minute GPU wait. (No SQLAlchemy import on purpose: they only call .commit()/.rollback().)
"""
import logging

logger = logging.getLogger(__name__)


def safe_rollback(db) -> bool:
    """Roll back, tolerating a connection that is already dead ("SSL connection has been closed unexpectedly").
    The first failed rollback makes SQLAlchemy discard the dead connection, so the second attempt works."""
    for _ in (1, 2):
        try:
            db.rollback()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Database rollback failed (%s) - retrying on a fresh connection.", str(e)[:120])
    return False


def release_connection(db) -> None:
    """Save pending changes and hand the connection back to the pool BEFORE a long wait (GPU server, LLM, upload).
    Otherwise the session sits 'idle in transaction' for minutes and the database server kills the connection."""
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Couldn't commit before a long wait (%s) - continuing on a fresh connection.", str(e)[:120])
        safe_rollback(db)
