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
Unit tests for chart_summarizer.utils.crypto

Covers:
  - generate_key: length, uniqueness
  - _resolve_key: explicit key, env var, missing raises
  - encrypt_phi / decrypt_phi: round-trip, wrong key, tampered ciphertext,
    truncated blob, non-ASCII UTF-8
  - hash_patient_id: determinism, salt effect, env-var salt, empty salt
"""

import base64
import os
from unittest.mock import patch

import pytest

from chart_summarizer.utils.crypto import (
    _KEY_BYTES,
    _NONCE_BYTES,
    _resolve_key,
    decrypt_phi,
    encrypt_phi,
    generate_key,
    hash_patient_id,
)


# ---------------------------------------------------------------------------
# generate_key
# ---------------------------------------------------------------------------


class TestGenerateKey:
    def test_returns_32_bytes(self) -> None:
        key = generate_key()
        assert isinstance(key, bytes)
        assert len(key) == _KEY_BYTES

    def test_two_keys_are_different(self) -> None:
        assert generate_key() != generate_key()


# ---------------------------------------------------------------------------
# _resolve_key
# ---------------------------------------------------------------------------


class TestResolveKey:
    def test_explicit_key_returned(self) -> None:
        key = os.urandom(_KEY_BYTES)
        assert _resolve_key(key) == key

    def test_explicit_key_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError, match="32 bytes"):
            _resolve_key(b"short")

    def test_env_var_key_returned(self) -> None:
        key = os.urandom(_KEY_BYTES)
        encoded = base64.b64encode(key).decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": encoded}):
            assert _resolve_key(None) == key

    def test_env_var_wrong_length_raises(self) -> None:
        # 16 bytes encoded
        encoded = base64.b64encode(os.urandom(16)).decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": encoded}):
            with pytest.raises(ValueError, match="32 bytes"):
                _resolve_key(None)

    def test_env_var_bad_base64_raises(self) -> None:
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "!!!not-base64!!!"}):
            with pytest.raises(ValueError, match="not valid base64"):
                _resolve_key(None)

    def test_no_key_no_env_raises(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ENCRYPTION_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="No encryption key"):
                _resolve_key(None)


# ---------------------------------------------------------------------------
# encrypt_phi / decrypt_phi — round-trips
# ---------------------------------------------------------------------------


class TestEncryptDecryptRoundTrip:
    def test_basic_round_trip(self) -> None:
        key = generate_key()
        plaintext = "John Doe — hypertension"
        token = encrypt_phi(plaintext, key=key)
        assert decrypt_phi(token, key=key) == plaintext

    def test_empty_string_round_trip(self) -> None:
        key = generate_key()
        token = encrypt_phi("", key=key)
        assert decrypt_phi(token, key=key) == ""

    def test_non_ascii_utf8_round_trip(self) -> None:
        key = generate_key()
        plaintext = "Ünïcödé — 中文 — العربية"
        token = encrypt_phi(plaintext, key=key)
        assert decrypt_phi(token, key=key) == plaintext

    def test_long_text_round_trip(self) -> None:
        key = generate_key()
        plaintext = "X" * 10_000
        token = encrypt_phi(plaintext, key=key)
        assert decrypt_phi(token, key=key) == plaintext

    def test_output_is_url_safe_base64(self) -> None:
        key = generate_key()
        token = encrypt_phi("test", key=key)
        # Should not raise; url-safe alphabet only
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        assert len(decoded) >= _NONCE_BYTES + 16  # nonce + tag minimum

    def test_same_plaintext_gives_different_ciphertexts(self) -> None:
        # Fresh nonce per call → different outputs
        key = generate_key()
        t1 = encrypt_phi("same text", key=key)
        t2 = encrypt_phi("same text", key=key)
        assert t1 != t2

    def test_uses_env_var_key_when_none_given(self) -> None:
        key = generate_key()
        encoded = base64.b64encode(key).decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": encoded}):
            token = encrypt_phi("env-key test")
            result = decrypt_phi(token)  # key=None → env var
        assert result == "env-key test"


# ---------------------------------------------------------------------------
# decrypt_phi — error cases
# ---------------------------------------------------------------------------


class TestDecryptErrors:
    def test_wrong_key_raises(self) -> None:
        key1 = generate_key()
        key2 = generate_key()
        token = encrypt_phi("secret", key=key1)
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt_phi(token, key=key2)

    def test_tampered_ciphertext_raises(self) -> None:
        key = generate_key()
        token = encrypt_phi("original", key=key)
        blob = bytearray(base64.urlsafe_b64decode(token.encode("ascii")))
        # Flip a byte in the ciphertext body (after nonce)
        blob[_NONCE_BYTES] ^= 0xFF
        tampered = base64.urlsafe_b64encode(bytes(blob)).decode("ascii")
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt_phi(tampered, key=key)

    def test_truncated_blob_raises(self) -> None:
        # Fewer than nonce + tag bytes
        short = base64.urlsafe_b64encode(b"\x00" * 10).decode("ascii")
        with pytest.raises(ValueError, match="too short"):
            decrypt_phi(short, key=generate_key())

    def test_invalid_base64_raises(self) -> None:
        with pytest.raises(ValueError, match="not valid base64"):
            decrypt_phi("!!!invalid!!!", key=generate_key())


# ---------------------------------------------------------------------------
# hash_patient_id
# ---------------------------------------------------------------------------


class TestHashPatientId:
    def test_returns_hex_string(self) -> None:
        h = hash_patient_id("12345")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 = 32 bytes = 64 hex chars
        int(h, 16)  # raises if not hex

    def test_deterministic(self) -> None:
        h1 = hash_patient_id("P-001", salt="fixed")
        h2 = hash_patient_id("P-001", salt="fixed")
        assert h1 == h2

    def test_different_ids_different_hashes(self) -> None:
        assert hash_patient_id("P-001") != hash_patient_id("P-002")

    def test_salt_changes_hash(self) -> None:
        h_no_salt = hash_patient_id("P-001", salt="")
        h_with_salt = hash_patient_id("P-001", salt="mysalt")
        assert h_no_salt != h_with_salt

    def test_env_var_salt_used_when_none(self) -> None:
        with patch.dict(os.environ, {"HASH_SALT": "envsalt"}):
            h_env = hash_patient_id("P-001")
        h_explicit = hash_patient_id("P-001", salt="envsalt")
        assert h_env == h_explicit

    def test_empty_env_salt_and_no_salt_give_same_result(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "HASH_SALT"}
        with patch.dict(os.environ, env, clear=True):
            h_default = hash_patient_id("P-001")
        h_empty = hash_patient_id("P-001", salt="")
        assert h_default == h_empty
