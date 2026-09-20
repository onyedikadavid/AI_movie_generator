import asyncio
from app.core.database import engine, Base
import app.models  # Ensures models are registered

async def create_tables():
    print(f"Connecting to: {engine.url}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created successfully via Base.metadata.create_all!")

if __name__ == "__main__":
    asyncio.run(create_tables())