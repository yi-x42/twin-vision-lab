
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from app.core.database import AsyncSessionLocal
from app.models.database import DataSource
from sqlalchemy import select

async def dump_cameras():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(DataSource).where(DataSource.source_type == 'camera'))
        cameras = result.scalars().all()
        
        with open("cameras_dump.txt", "w", encoding="utf-8") as f:
            f.write(f"{'ID':<5} | {'Name':<20} | {'Config':<50}\n")
            f.write("-" * 80 + "\n")
            for cam in cameras:
                f.write(f"{cam.id:<5} | {cam.name:<20} | {cam.config}\n")
        print("Dump written to cameras_dump.txt")

if __name__ == "__main__":
    asyncio.run(dump_cameras())
