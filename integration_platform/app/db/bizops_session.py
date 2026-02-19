"""
Async SQLAlchemy session factory for the BizOps Events database.

This is the SECOND on-prem database — completely separate from the
operational CRDB/ERDB database (app/db/session.py).

Why a dedicated engine?
  - Different connection string → different SQL Server database (or even
    a different SQL Server instance entirely).
  - Different pool sizing: BizOps is write-mostly (append-only audit log).
    Reads are infrequent BI/reporting queries, so a small pool is enough.
  - If the operational DB is unavailable the audit log continues writing,
    and vice-versa.

Configuration:
  Set BIZOPS_DATABASE_URL in environment / Azure Key Vault.
  Same format as DATABASE_URL:
    mssql+aioodbc://<user>:<pass>@<server>.database.windows.net:1433/<db>
    ?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_settings

settings = get_settings()

bizops_engine = create_async_engine(
    settings.BIZOPS_DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=3,        # small — BizOps DB is write-mostly (one INSERT per event)
    max_overflow=5,
    pool_timeout=30,
)

BizOpsSessionLocal = async_sessionmaker(
    bind=bizops_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_bizops_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a BizOps DB session."""
    async with BizOpsSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
