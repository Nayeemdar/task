"""
Async SQLAlchemy session factory — Azure SQL Database (single database).

Driver:  mssql+aioodbc  (pyodbc wrapped by aioodbc for asyncio)
Auth:    SQL login (username/password) or Azure AD / Managed Identity
         — controlled by the DATABASE_URL Application Setting.

Pool sizing:
  Azure Functions scales horizontally; each worker holds its own pool.
  Keep pool_size small (5) so that N workers × 5 connections stays within
  the Azure SQL DTU/vCore connection limit for your service tier.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=5,       # small — Azure Functions scales out, keep total connections bounded
    max_overflow=10,
    pool_timeout=30,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
