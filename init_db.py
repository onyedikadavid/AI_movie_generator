# init_db.py
import asyncio
import sys
import os

sys.path.insert(0, os.path.realpath("."))

from app.core.database import engine, Base
import app.models  # Load model definitions

async def init_models():
    async with engine.begin() as conn:
        print("Creating all tables in PostgreSQL...")
        await conn.run_sync(Base.metadata.create_all)
        print("Database sync complete!")

if __name__ == "__main__":
    asyncio.run(init_models())