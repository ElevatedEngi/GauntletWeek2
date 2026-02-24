# Copyright (C) 2026 OpenEMR Community
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
SQLAlchemy async engine and session factory.

Database selection is config-driven:
  - Development: sqlite+aiosqlite:///./chart_summarizer.db
  - Production:  postgresql+asyncpg://user:pass@host/db

Usage::

    from chart_summarizer.db.engine import get_db_session, init_db

    # FastAPI dependency
    async def my_route(db: AsyncSession = Depends(get_db_session)):
        ...

    # On startup
    await init_db()
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from chart_summarizer.config import settings
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)

# Module-level singletons — created lazily on first access.
_engine = None
_session_factory: async_sessionmaker | None = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            # SQLite needs this to allow connection reuse across threads.
            connect_args={"check_same_thread": False}
            if "sqlite" in settings.DATABASE_URL
            else {},
        )
        logger.info("Database engine created | url_scheme=%s", settings.DATABASE_URL.split(":")[0])
    return _engine


def _get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(),
            expire_on_commit=False,
        )
    return _session_factory


async def init_db() -> None:
    """
    Create all tables defined in the ORM if they do not already exist.

    Called once on application startup. Safe to call on every restart —
    SQLAlchemy uses CREATE TABLE IF NOT EXISTS semantics.
    """
    from chart_summarizer.db.models import Base

    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialised.")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: yield a transactional async database session.

    The session is automatically closed (and rolled back on error) when
    the request completes.

    Usage::

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db_session)):
            result = await db.execute(select(AuditLog))
    """
    factory = _get_session_factory()
    async with factory() as session:
        yield session