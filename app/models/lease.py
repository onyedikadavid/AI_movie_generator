from sqlalchemy import Column, Integer, String, DateTime
from app.core.database import Base


class PipelineLease(Base):
    """
    Single-row mutex (id is always 1) guaranteeing that only ONE project is
    being processed at a time across *every* worker process.

    Why a table instead of a Postgres advisory lock: the database here is
    reached through Neon's pooled (PgBouncer, transaction-mode) endpoint,
    where session-level advisory locks silently don't hold. A row that is
    updated with a compare-and-swap UPDATE behaves correctly through any
    pooler. The holder refreshes heartbeat_at while running; if it dies the
    lease simply goes stale and the next worker takes it over.
    """
    __tablename__ = "pipeline_lease"

    id = Column(Integer, primary_key=True)
    project_id = Column(String, nullable=True)
    holder = Column(String, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
