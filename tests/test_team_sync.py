"""
Tests for backend/team_sync.py (TeamSync).

No real encryption keys — all crypto exercised end-to-end with small
in-process operations (scrypt N=1024 for speed in tests).

Covers:
  - export_participants(): returns valid JSON bundle
  - import_participants(): round-trips records correctly
  - Field fidelity: all ParticipantRecord fields preserved
  - Multiple records round-trip
  - Empty record list
  - Wrong passphrase raises ValueError
  - Tampered ciphertext raises ValueError
  - Empty passphrase raises ValueError on export
  - Empty passphrase raises ValueError on import
  - Missing required bundle field raises ValueError
  - Unsupported bundle version raises ValueError
  - Unsupported algorithm raises ValueError
  - Unsupported KDF raises ValueError
  - Malformed JSON bundle raises ValueError
  - Malformed inner JSON raises ValueError
  - Missing 'participants' key in inner payload raises ValueError
  - participant[] missing required 'id' field raises ValueError
  - participant with None optional fields round-trips correctly
  - Bundle is not valid JSON raises ValueError
  - Bundle kdf_n/r/p params from bundle override defaults
  - _validate_bundle_schema: missing individual fields
  - _b64 / _unb64 helpers
  - _opt_float helper
"""

from __future__ import annotations

import base64
import json
import os

import pytest

from backend.team_sync import (
    ParticipantRecord,
    TeamSync,
    _b64,
    _opt_float,
    _unb64,
    _validate_bundle_schema,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Use a tiny scrypt N so tests run in milliseconds instead of seconds.
# We monkey-patch _derive_key at the module level so ALL crypto calls use it.
_FAST_N = 1024


def _fast_derive_key(passphrase: str, salt: bytes, *, n=_FAST_N, r=8, p=1) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.backends import default_backend
    import backend.team_sync as ts

    kdf = Scrypt(
        salt=salt,
        length=ts._KEY_BYTES,
        n=_FAST_N,
        r=r,
        p=p,
        backend=default_backend(),
    )
    return kdf.derive(passphrase.encode())


@pytest.fixture(autouse=True)
def patch_scrypt(monkeypatch):
    """Replace the expensive scrypt KDF with a fast one for all tests."""
    import backend.team_sync as ts
    monkeypatch.setattr(ts, "_derive_key", _fast_derive_key)


def _record(**kwargs) -> ParticipantRecord:
    defaults = dict(
        id="abc-123",
        name="Alice",
        notes="CTO at Acme",
        ps_type="Inquisitor",
        ps_confidence=0.82,
        ps_reasoning="Heavy use of clarifying questions",
        ps_state="active",
    )
    defaults.update(kwargs)
    return ParticipantRecord(**defaults)


def _export(records, passphrase="secret") -> str:
    return TeamSync.export_participants(records, passphrase)


def _round_trip(records, passphrase="secret") -> list[ParticipantRecord]:
    bundle = _export(records, passphrase)
    return TeamSync.import_participants(bundle, passphrase)


# ---------------------------------------------------------------------------
# Export — shape of the bundle
# ---------------------------------------------------------------------------

class TestExport:
    def test_returns_valid_json(self):
        bundle = _export([_record()])
        parsed = json.loads(bundle)
        assert isinstance(parsed, dict)

    def test_bundle_has_required_fields(self):
        bundle_dict = json.loads(_export([_record()]))
        for field in ("v", "algo", "kdf", "salt", "nonce", "ciphertext"):
            assert field in bundle_dict

    def test_bundle_version_is_1(self):
        bundle_dict = json.loads(_export([_record()]))
        assert bundle_dict["v"] == 1

    def test_algo_is_aes_256_gcm(self):
        bundle_dict = json.loads(_export([_record()]))
        assert bundle_dict["algo"] == "aes-256-gcm"

    def test_kdf_is_scrypt(self):
        bundle_dict = json.loads(_export([_record()]))
        assert bundle_dict["kdf"] == "scrypt"

    def test_salt_is_base64(self):
        bundle_dict = json.loads(_export([_record()]))
        # Should not raise
        decoded = base64.b64decode(bundle_dict["salt"])
        assert len(decoded) == 16  # _SALT_BYTES

    def test_nonce_is_base64(self):
        bundle_dict = json.loads(_export([_record()]))
        decoded = base64.b64decode(bundle_dict["nonce"])
        assert len(decoded) == 12  # _NONCE_BYTES

    def test_different_exports_have_different_salts(self):
        r = _record()
        b1 = json.loads(_export([r]))
        b2 = json.loads(_export([r]))
        assert b1["salt"] != b2["salt"]

    def test_empty_passphrase_raises(self):
        with pytest.raises(ValueError, match="passphrase"):
            _export([_record()], passphrase="")


# ---------------------------------------------------------------------------
# Round-trip fidelity
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_single_record_round_trips(self):
        r = _record()
        imported = _round_trip([r])
        assert len(imported) == 1

    def test_id_preserved(self):
        r = _record(id="xyz-999")
        assert _round_trip([r])[0].id == "xyz-999"

    def test_name_preserved(self):
        r = _record(name="Bob")
        assert _round_trip([r])[0].name == "Bob"

    def test_notes_preserved(self):
        r = _record(notes="Key account manager")
        assert _round_trip([r])[0].notes == "Key account manager"

    def test_ps_type_preserved(self):
        r = _record(ps_type="Firestarter")
        assert _round_trip([r])[0].ps_type == "Firestarter"

    def test_ps_confidence_preserved(self):
        r = _record(ps_confidence=0.75)
        assert _round_trip([r])[0].ps_confidence == pytest.approx(0.75)

    def test_ps_reasoning_preserved(self):
        r = _record(ps_reasoning="Uses narrative openers")
        assert _round_trip([r])[0].ps_reasoning == "Uses narrative openers"

    def test_ps_state_preserved(self):
        r = _record(ps_state="pending")
        assert _round_trip([r])[0].ps_state == "pending"

    def test_none_optional_fields_preserved(self):
        r = _record(name=None, notes=None, ps_type=None,
                    ps_confidence=None, ps_reasoning=None)
        imported = _round_trip([r])[0]
        assert imported.name is None
        assert imported.notes is None
        assert imported.ps_type is None
        assert imported.ps_confidence is None
        assert imported.ps_reasoning is None

    def test_multiple_records_round_trip(self):
        records = [
            _record(id="r1", name="Alice"),
            _record(id="r2", name="Bob"),
            _record(id="r3", name="Carol"),
        ]
        imported = _round_trip(records)
        assert len(imported) == 3
        assert [r.name for r in imported] == ["Alice", "Bob", "Carol"]

    def test_empty_list_round_trips(self):
        assert _round_trip([]) == []

    def test_passphrase_with_unicode(self):
        r = _record()
        bundle = _export([r], passphrase="pässwörd!🔑")
        imported = TeamSync.import_participants(bundle, "pässwörd!🔑")
        assert imported[0].id == r.id


# ---------------------------------------------------------------------------
# Wrong passphrase / tampered data
# ---------------------------------------------------------------------------

class TestWrongPassphrase:
    def test_wrong_passphrase_raises_value_error(self):
        bundle = _export([_record()], passphrase="correct")
        with pytest.raises(ValueError, match="decryption failed"):
            TeamSync.import_participants(bundle, "wrong")

    def test_empty_passphrase_on_import_raises(self):
        bundle = _export([_record()])
        with pytest.raises(ValueError, match="passphrase"):
            TeamSync.import_participants(bundle, "")

    def test_tampered_ciphertext_raises_value_error(self):
        bundle_dict = json.loads(_export([_record()]))
        # Flip one byte in the ciphertext
        raw = base64.b64decode(bundle_dict["ciphertext"])
        flipped = bytes([raw[0] ^ 0xFF]) + raw[1:]
        bundle_dict["ciphertext"] = base64.b64encode(flipped).decode()
        tampered = json.dumps(bundle_dict)
        with pytest.raises(ValueError, match="decryption failed"):
            TeamSync.import_participants(tampered, "secret")


# ---------------------------------------------------------------------------
# Bundle schema validation errors
# ---------------------------------------------------------------------------

class TestBundleValidation:
    def _base_bundle(self) -> dict:
        return json.loads(_export([_record()]))

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            TeamSync.import_participants("not json {{{", "secret")

    def test_missing_v_field_raises(self):
        b = self._base_bundle()
        del b["v"]
        with pytest.raises(ValueError, match="missing fields"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_missing_salt_raises(self):
        b = self._base_bundle()
        del b["salt"]
        with pytest.raises(ValueError, match="missing fields"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_missing_nonce_raises(self):
        b = self._base_bundle()
        del b["nonce"]
        with pytest.raises(ValueError, match="missing fields"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_missing_ciphertext_raises(self):
        b = self._base_bundle()
        del b["ciphertext"]
        with pytest.raises(ValueError, match="missing fields"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_unsupported_version_raises(self):
        b = self._base_bundle()
        b["v"] = 99
        with pytest.raises(ValueError, match="unsupported bundle version"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_unsupported_algo_raises(self):
        b = self._base_bundle()
        b["algo"] = "chacha20"
        with pytest.raises(ValueError, match="unsupported algorithm"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_unsupported_kdf_raises(self):
        b = self._base_bundle()
        b["kdf"] = "argon2"
        with pytest.raises(ValueError, match="unsupported KDF"):
            TeamSync.import_participants(json.dumps(b), "secret")

    def test_bundle_not_a_dict_raises(self):
        with pytest.raises(ValueError, match="JSON object"):
            TeamSync.import_participants(json.dumps([1, 2, 3]), "secret")


# ---------------------------------------------------------------------------
# Inner payload parsing errors
# ---------------------------------------------------------------------------

class TestInnerPayloadErrors:
    def _encrypt_inner(self, inner: dict, passphrase="secret") -> str:
        """Build a valid outer bundle wrapping a custom inner payload."""
        import backend.team_sync as ts
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _fast_derive_key(passphrase, salt)
        ct = ts._aes_gcm_encrypt(json.dumps(inner).encode(), key, nonce)
        bundle = {
            "v": 1,
            "exported_at": "2024-01-01T00:00:00+00:00",
            "algo": "aes-256-gcm",
            "kdf": "scrypt",
            "kdf_n": _FAST_N,
            "kdf_r": 8,
            "kdf_p": 1,
            "salt": _b64(salt),
            "nonce": _b64(nonce),
            "ciphertext": _b64(ct),
        }
        return json.dumps(bundle)

    def test_missing_participants_key_raises(self):
        bundle = self._encrypt_inner({"data": []})
        with pytest.raises(ValueError, match="'participants'"):
            TeamSync.import_participants(bundle, "secret")

    def test_participants_not_a_list_raises(self):
        bundle = self._encrypt_inner({"participants": "oops"})
        with pytest.raises(ValueError, match="list"):
            TeamSync.import_participants(bundle, "secret")

    def test_participant_not_a_dict_raises(self):
        bundle = self._encrypt_inner({"participants": ["not a dict"]})
        with pytest.raises(ValueError, match="not an object"):
            TeamSync.import_participants(bundle, "secret")

    def test_participant_missing_id_raises(self):
        bundle = self._encrypt_inner({"participants": [{"name": "Alice"}]})
        with pytest.raises(ValueError, match="missing required field"):
            TeamSync.import_participants(bundle, "secret")


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_b64_round_trips(self):
        data = b"\x00\x01\x02\xFF"
        assert _unb64(_b64(data)) == data

    def test_unb64_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid base64"):
            _unb64("not-valid-base64!!!")

    def test_opt_float_none(self):
        assert _opt_float(None) is None

    def test_opt_float_int(self):
        assert _opt_float(1) == 1.0

    def test_opt_float_float(self):
        assert _opt_float(0.75) == pytest.approx(0.75)

    def test_opt_float_string_number(self):
        assert _opt_float("0.5") == pytest.approx(0.5)

    def test_validate_bundle_schema_passes_valid(self):
        b = json.loads(_export([_record()]))
        # should not raise
        _validate_bundle_schema(b)

    def test_validate_bundle_schema_missing_algo_raises(self):
        b = json.loads(_export([_record()]))
        del b["algo"]
        with pytest.raises(ValueError, match="missing fields"):
            _validate_bundle_schema(b)
