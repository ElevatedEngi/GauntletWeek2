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
HIPAA-safe structured logging.

When HIPAA_MODE=True, this module intercepts all log records and redacts
any PHI patterns (patient names, DOBs, SSNs) before they reach the handler.

Safe to log: patient PID, request IDs, model names, token counts, latency.
Never log: patient names, dates of birth, SSNs, addresses, phone numbers.
"""

import logging
import re
from typing import Optional

from chart_summarizer.config import settings


# ---------------------------------------------------------------------------
# PHI redaction patterns
# ---------------------------------------------------------------------------

_PHI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Social Security Number: 123-45-6789 or 123456789
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN-REDACTED]"),
    (re.compile(r"\b\d{9}\b"), "[SSN-REDACTED]"),
    # US phone numbers
    (re.compile(r"\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE-REDACTED]"),
    # Dates that look like DOBs: MM/DD/YYYY or MM-DD-YYYY
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b"), "[DATE-REDACTED]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL-REDACTED]"),
]


def redact_phi(message: str) -> str:
    """
    Apply PHI redaction patterns to a log message string.

    Args:
        message: Raw log message that may contain PHI.

    Returns:
        Message with all matched PHI patterns replaced by redaction tokens.
    """
    for pattern, replacement in _PHI_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


# ---------------------------------------------------------------------------
# HIPAA-aware log filter
# ---------------------------------------------------------------------------


class HIPAAFilter(logging.Filter):
    """
    Logging filter that redacts PHI from log records when HIPAA_MODE is enabled.

    Attach this filter to any handler that writes to external systems
    (files, CloudWatch, Splunk, etc.).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact PHI from the log record message and any string arguments."""
        if settings.HIPAA_MODE:
            record.msg = redact_phi(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: redact_phi(str(v)) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact_phi(str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
        return True


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Create (or retrieve) a named logger with HIPAA-safe defaults.

    Args:
        name: Logger name, typically __name__ of the calling module.
        level: Override log level. Defaults to settings.LOG_LEVEL.

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(HIPAAFilter())
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    effective_level = level or settings.LOG_LEVEL
    logger.setLevel(getattr(logging, effective_level, logging.INFO))

    return logger
