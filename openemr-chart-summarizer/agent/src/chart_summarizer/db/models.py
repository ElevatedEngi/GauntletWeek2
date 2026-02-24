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
SQLAlchemy async ORM models for the audit log and summary cache.

HIPAA compliance:
  - AuditLog stores only PIDs (patient IDs) and metadata — never PHI.
  - AuditLog rows are append-only; no UPDATE/DELETE is ever issued.
  - SummaryCache stores serialised SummaryResponse JSON. The JSON itself
    contains summary_text which may include clinical data; in production
    this table should reside on an encrypted-at-rest volume.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    """
    Immutable HIPAA audit record for every patient-data request.

    Append-only — the application must never UPDATE or DELETE rows.
    Only patient PIDs and operational metadata are stored; PHI is
    never written here.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        comment="UTC timestamp of the request (ISO 8601).",
    )
    request_id = Column(
        String(36),
        nullable=True,
        index=True,
        comment="UUID linking this audit entry to the summary request.",
    )
    user_id = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Authenticated user ID from the OAuth2 token or shared key.",
    )
    patient_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="OpenEMR patient PID — NOT name, DOB, or other PHI.",
    )
    action = Column(
        String(64),
        nullable=False,
        comment="Operation performed: summarize | view | feedback.",
    )
    outcome = Column(
        String(32),
        nullable=False,
        comment="Result: success | partial | failure | rate_limited.",
    )
    response_time_ms = Column(
        Integer,
        nullable=False,
        default=0,
        comment="End-to-end request latency in milliseconds.",
    )
    llm_model = Column(
        String(128),
        nullable=True,
        comment="LLM model identifier used for this request.",
    )
    token_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Total tokens consumed (input + output).",
    )
    cost_estimate = Column(
        Float,
        nullable=True,
        comment="Estimated USD cost based on provider pricing.",
    )


class SummaryCache(Base):
    """
    Short-lived cache of generated SummaryResponse objects.

    Keyed by summary_id (= SummaryMetadata.request_id UUID).
    Entries expire after SUMMARY_CACHE_TTL_HOURS hours.
    """

    __tablename__ = "summary_cache"

    id = Column(
        String(36),
        primary_key=True,
        comment="UUID = SummaryMetadata.request_id — used as the public summary_id.",
    )
    patient_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="OpenEMR patient PID.",
    )
    specialty = Column(
        String(64),
        nullable=False,
        default="primary_care",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    expires_at = Column(
        DateTime,
        nullable=False,
        index=True,
        comment="Row is logically expired after this timestamp.",
    )
    summary_json = Column(
        Text,
        nullable=False,
        comment="JSON-serialised SummaryResponse. May contain clinical data.",
    )
    confidence_score = Column(
        Float,
        nullable=True,
        comment="Verification confidence score (0.0 – 1.0).",
    )
    created_by = Column(
        String(255),
        nullable=True,
        comment="user_id of the clinician who requested this summary.",
    )