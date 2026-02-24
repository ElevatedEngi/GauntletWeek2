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
Shared FHIR R4 parsing utilities used by all real FHIR tool implementations.

Kept in a separate module (not __init__.py) to avoid circular imports when
tool submodules need to import these helpers.
"""

from datetime import date, datetime
from typing import Any, Optional


def extract_bundle_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract resource dicts from a FHIR Bundle searchset response.

    Args:
        bundle: Raw FHIR Bundle JSON dict from the API.

    Returns:
        List of resource dicts (the ``resource`` key from each entry).
        Returns an empty list if the bundle has no entries.
    """
    entries = bundle.get("entry", []) or []
    return [e["resource"] for e in entries if "resource" in e]


def parse_fhir_date(value: Optional[str]) -> Optional[date]:
    """
    Parse a FHIR date string (``YYYY-MM-DD``) into a Python ``date``.

    Returns None if the value is absent or unparseable.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_fhir_datetime(value: Optional[str]) -> Optional[datetime]:
    """
    Parse a FHIR dateTime string into a Python ``datetime``.

    Handles both date-only (``YYYY-MM-DD``) and full datetime formats.
    Returns None if the value is absent or unparseable.
    """
    if not value:
        return None
    try:
        # Normalise: strip trailing Z, replace space with T
        value = value.rstrip("Z").replace(" ", "T")
        if "T" in value:
            return datetime.fromisoformat(value)
        return datetime.fromisoformat(value + "T00:00:00")
    except ValueError:
        return None


def get_coding_display(element: Optional[dict[str, Any]], fallback: str = "") -> str:
    """
    Extract the ``display`` text from the first coding in a CodeableConcept.

    Falls back to ``text`` on the CodeableConcept, then to ``fallback``.

    Args:
        element: A FHIR CodeableConcept dict (has ``coding`` and/or ``text``).
        fallback: Returned when no display text can be found.
    """
    if not element:
        return fallback
    codings = element.get("coding") or []
    if codings:
        display = codings[0].get("display") or codings[0].get("code") or ""
        if display:
            return display
    return element.get("text") or fallback


def get_coding_code(
    element: Optional[dict[str, Any]],
    system_substring: str = "",
) -> Optional[str]:
    """
    Extract a coding code from a CodeableConcept, optionally filtering by system URL.

    Args:
        element: A FHIR CodeableConcept dict.
        system_substring: If provided, only consider codings whose ``system``
                          URL contains this substring (e.g. ``"loinc"``, ``"icd-10"``).

    Returns:
        The code string, or None if not found.
    """
    if not element:
        return None
    codings = element.get("coding") or []
    for coding in codings:
        system = coding.get("system", "")
        if system_substring and system_substring.lower() not in system.lower():
            continue
        code = coding.get("code")
        if code:
            return code
    return None
