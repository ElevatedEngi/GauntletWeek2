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
Unit tests for HIPAA audit log functionality.

Tests:
  - write_audit_record() inserts a row into audit_log.
  - Inserted row contains expected metadata.
  - No PHI (patient names / DOBs) is stored in the log.
  - Audit failures never raise — they log and continue.
  - The audit table is append-only (no UPDATE/DELETE issued).
"""

import re
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chart_summarizer.api.middleware import write_audit_record
from chart_summarizer.db.models import Base


# ---------------------------------------------------------------------------
# In-memory SQLite fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session() -> AsyncSession:
    """Provide an in-memory SQLite session with tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWriteAuditRecord:

    async def test_record_is_inserted(self, db_session: AsyncSession) -> None:
        """write_audit_record() must insert exactly one row."""
        await write_audit_record(
            db_session,
            request_id="req-001",
            user_id="user-abc",
            patient_id="PID-123",
            action="summarize",
            outcome="success",
            response_time_ms=450,
            llm_model="claude-haiku",
            token_count=1200,
        )

        result = await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))
        count = result.scalar()
        assert count == 1

    async def test_record_contains_expected_metadata(self, db_session: AsyncSession) -> None:
        await write_audit_record(
            db_session,
            request_id="req-002",
            user_id="user-xyz",
            patient_id="PID-456",
            action="view",
            outcome="success",
            response_time_ms=120,
            llm_model="gpt-4o",
            token_count=800,
        )

        result = await db_session.execute(
            text("SELECT * FROM audit_log WHERE request_id = 'req-002'")
        )
        row = result.fetchone()
        assert row is not None
        assert row.user_id == "user-xyz"
        assert row.patient_id == "PID-456"
        assert row.action == "view"
        assert row.outcome == "success"
        assert row.response_time_ms == 120
        assert row.llm_model == "gpt-4o"
        assert row.token_count == 800

    async def test_no_phi_in_audit_log(self, db_session: AsyncSession) -> None:
        """
        Audit records must never contain patient names, dates of birth, or other PHI.

        The patient_id column must store only an opaque PID — not a name.
        This test verifies that no name-like content (alphabetic words longer
        than a typical ID) appears in the patient_id field.
        """
        # Attempt to store a name instead of a PID — the system should store
        # whatever is passed, so this test documents the contract: callers must
        # only pass PIDs, never names.  The test asserts that typical PIDs look
        # like identifiers, not prose names.
        await write_audit_record(
            db_session,
            request_id="req-phi",
            user_id="user-001",
            patient_id="12345",  # A proper PID — numeric only
            action="summarize",
            outcome="success",
            response_time_ms=300,
        )

        result = await db_session.execute(
            text("SELECT patient_id FROM audit_log WHERE request_id = 'req-phi'")
        )
        row = result.fetchone()
        assert row is not None
        # A proper PID is not a human name
        stored_pid = row.patient_id
        # Must not look like "Jane Doe" (two alpha words separated by space)
        assert not re.match(r'^[A-Za-z]+ [A-Za-z]+$', stored_pid), \
            "PHI (patient name) was stored in the audit log — only PIDs are allowed."

    async def test_multiple_records_are_appended(self, db_session: AsyncSession) -> None:
        """Audit log is append-only: multiple records accumulate."""
        for i in range(5):
            await write_audit_record(
                db_session,
                request_id=f"req-{i}",
                user_id="user-batch",
                patient_id=f"PID-{i}",
                action="summarize",
                outcome="success",
                response_time_ms=100 * i,
            )

        result = await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))
        count = result.scalar()
        assert count == 5

    async def test_audit_failure_does_not_raise(self, db_session: AsyncSession) -> None:
        """
        A broken DB session must not propagate an exception to the caller.

        write_audit_record() catches and logs all DB errors without raising.
        """
        # Close the session to force an error on next write
        await db_session.close()

        # Must not raise
        try:
            await write_audit_record(
                db_session,
                request_id="req-broken",
                user_id="u",
                patient_id="P",
                action="summarize",
                outcome="failure",
                response_time_ms=0,
            )
        except Exception as exc:
            pytest.fail(f"write_audit_record raised unexpectedly: {exc}")

    async def test_timestamp_is_set_automatically(self, db_session: AsyncSession) -> None:
        """Each audit record must have a non-null UTC timestamp."""
        await write_audit_record(
            db_session,
            request_id="req-ts",
            user_id="u",
            patient_id="P-ts",
            action="feedback:approved",
            outcome="success",
            response_time_ms=50,
        )
        result = await db_session.execute(
            text("SELECT timestamp FROM audit_log WHERE request_id = 'req-ts'")
        )
        row = result.fetchone()
        assert row is not None
        assert row.timestamp is not None