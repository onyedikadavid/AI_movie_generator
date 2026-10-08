"""Checks that a dropped database connection (the 'SSL connection has been closed unexpectedly' failure seen after long
GPU waits) is survived instead of masking the real error. No database needed - a fake session behaves like the real failure."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.core.db_safety import release_connection, safe_rollback


class OperationalError(Exception):
    pass


class DeadThenFreshDB:
    """The first rollback/commit hits the dead connection and fails; SQLAlchemy then discards that connection, so later calls work."""
    def __init__(self, dead_commit=False, dead_rollbacks=1):
        self.dead_commit, self.dead_rollbacks = dead_commit, dead_rollbacks
        self.commits = self.rollbacks = 0

    def commit(self):
        if self.dead_commit:
            self.dead_commit = False
            raise OperationalError("SSL connection has been closed unexpectedly")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self.dead_rollbacks > 0:
            self.dead_rollbacks -= 1
            raise OperationalError("SSL connection has been closed unexpectedly")


# 1. the exact failure from the logs: rollback on a dead connection must not raise
db = DeadThenFreshDB(dead_rollbacks=1)
assert safe_rollback(db) is True and db.rollbacks == 2
print("OK 1: rollback on a dropped connection succeeds on the second try (the old code crashed here)")

# 2. even if it keeps failing, it never raises (so the ORIGINAL error stays visible)
assert safe_rollback(DeadThenFreshDB(dead_rollbacks=5)) is False
print("OK 2: a persistently dead connection never raises out of the error handler")

# 3. release_connection commits before a long wait when the connection is healthy
db = DeadThenFreshDB(dead_rollbacks=0); release_connection(db); assert db.commits == 1
print("OK 3: connection is released (committed) before a long wait")

# 4. ...and survives the commit failing too
db = DeadThenFreshDB(dead_commit=True, dead_rollbacks=1); release_connection(db)
print("OK 4: a dead connection at release time is recovered, not fatal")
print("ALL DB SAFETY CHECKS PASSED")
