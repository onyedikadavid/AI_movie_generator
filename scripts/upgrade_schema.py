"""
Manual schema upgrade (the API and worker also do this automatically on start).

    python scripts/upgrade_schema.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
logging.basicConfig(level=logging.INFO)

from app.core.schema_upgrade import ensure_schema  # noqa: E402
from app.core.sync_db import engine  # noqa: E402

if __name__ == "__main__":
    ensure_schema(engine)
    print("Done.")
