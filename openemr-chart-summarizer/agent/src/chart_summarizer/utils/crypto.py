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
Encryption helpers for PHI at rest.

HIPAA requires PHI stored at rest to be encrypted with AES-256.
This module provides helpers for:
  - Encrypting summary content before database storage
  - Decrypting stored summaries for retrieval
  - Generating and rotating encryption keys
  - One-way hashing of patient IDs for safe use in trace/log IDs

Wire format for encrypt_phi / decrypt_phi:
  base64url( nonce[12] || ciphertext_with_tag )

  - nonce  : 12 bytes, random per call (GCM standard)
  - ciphertext_with_tag : output of AESGCM.encrypt()
    which appends the 16-byte GCM authentication tag automatically

Key management:
  In production, keys MUST come from AWS Secrets Manager or an equivalent
  secrets backend. For local development / CI, pass the key explicitly or
  set the ENCRYPTION_KEY environment variable (base64-encoded 32 bytes).
  Never store keys in source code.
"""

import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Byte lengths (GCM constants)
_KEY_BYTES = 32   # AES-256
_NONCE_BYTES = 12  # 96-bit nonce — mandatory for GCM interoperability


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def generate_key() -> bytes:
    """
    Generate a cryptographically secure 32-byte AES-256 key.

    Returns:
        32 random bytes suitable for AES-256-GCM.

    Note:
        Store the returned key in AWS Secrets Manager immediately.
        Never log or persist keys in plaintext.
    """
    return os.urandom(_KEY_BYTES)


def _resolve_key(key: Optional[bytes]) -> bytes:
    """
    Return the encryption key to use.

    Priority:
    1. Explicit ``key`` argument (tests / in-process key rotation).
    2. ENCRYPTION_KEY environment variable (base64-encoded 32 bytes).

    Raises:
        ValueError: If no key is available or the key is not 32 bytes.
    """
    if key is not None:
        if len(key) != _KEY_BYTES:
            raise ValueError(
                f"AES-256 key must be exactly {_KEY_BYTES} bytes; "
                f"got {len(key)}."
            )
        return key

    raw_env = os.environ.get("ENCRYPTION_KEY", "")
    if raw_env:
        try:
            decoded = base64.b64decode(raw_env)
        except Exception as exc:
            raise ValueError(
                "ENCRYPTION_KEY environment variable is not valid base64."
            ) from exc
        if len(decoded) != _KEY_BYTES:
            raise ValueError(
                f"ENCRYPTION_KEY must decode to {_KEY_BYTES} bytes; "
                f"got {len(decoded)}."
            )
        return decoded

    raise ValueError(
        "No encryption key provided. Pass key= explicitly or set "
        "the ENCRYPTION_KEY environment variable (base64-encoded 32 bytes)."
    )


# ---------------------------------------------------------------------------
# Encryption / decryption
# ---------------------------------------------------------------------------


def encrypt_phi(plaintext: str, key: Optional[bytes] = None) -> str:
    """
    Encrypt a string containing PHI using AES-256-GCM.

    A fresh 12-byte nonce is generated for every call. The output encodes
    ``nonce || ciphertext_with_tag`` as URL-safe base64, making it safe
    to store in any text column.

    Args:
        plaintext: The PHI string to encrypt (UTF-8 encoded internally).
        key: 32-byte AES key. If None, resolved from ENCRYPTION_KEY env var.

    Returns:
        URL-safe base64 string: ``base64url( nonce[12] || ciphertext_with_tag )``.

    Raises:
        ValueError: If the key is not 32 bytes or not available.
    """
    resolved = _resolve_key(key)
    aesgcm = AESGCM(resolved)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    blob = nonce + ciphertext_with_tag
    return base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_phi(ciphertext: str, key: Optional[bytes] = None) -> str:
    """
    Decrypt a base64-encoded AES-256-GCM ciphertext produced by encrypt_phi().

    Args:
        ciphertext: URL-safe base64 string from encrypt_phi().
        key: 32-byte AES key. If None, resolved from ENCRYPTION_KEY env var.

    Returns:
        Original plaintext string (UTF-8).

    Raises:
        ValueError: If decryption fails (wrong key, tampered data, or bad format).
    """
    resolved = _resolve_key(key)
    try:
        blob = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except Exception as exc:
        raise ValueError("ciphertext is not valid base64.") from exc

    if len(blob) < _NONCE_BYTES + 16:  # 16 = minimum GCM tag length
        raise ValueError(
            f"ciphertext too short: expected at least {_NONCE_BYTES + 16} bytes "
            f"after decoding, got {len(blob)}."
        )

    nonce = blob[:_NONCE_BYTES]
    encrypted = blob[_NONCE_BYTES:]

    try:
        aesgcm = AESGCM(resolved)
        plaintext_bytes = aesgcm.decrypt(nonce, encrypted, None)
    except Exception as exc:
        raise ValueError(
            "Decryption failed — wrong key, corrupted data, or modified ciphertext."
        ) from exc

    return plaintext_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Patient ID hashing
# ---------------------------------------------------------------------------


def hash_patient_id(patient_id: str, salt: Optional[str] = None) -> str:
    """
    Create a one-way SHA-256 hash of a patient ID for safe use in trace IDs.

    Allows log correlation without exposing raw PIDs in observability systems
    (LangSmith, CloudWatch, etc.).

    Args:
        patient_id: The raw OpenEMR patient PID.
        salt: Optional salt string. Defaults to HASH_SALT env var, then "".

    Returns:
        Lowercase hex-encoded SHA-256 digest of ``salt + patient_id``.
    """
    if salt is None:
        salt = os.environ.get("HASH_SALT", "")
    material = (salt + patient_id).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
