"""
Team Intelligence JSON export / import with AES-256-GCM encryption.

Architecture
────────────
  Participant records (in-memory dicts)
       │
       │  export_participants(records, passphrase)
       ▼
  Encrypted bundle (JSON string)
       │  base64(salt) + base64(nonce) + base64(ciphertext)
       │  key derived from passphrase via scrypt
       ▼
  import_participants(bundle_json, passphrase)
       │
       ▼
  list[ParticipantRecord]   (caller merges into local SQLite)

Encryption scheme
─────────────────
  KDF     : scrypt (N=2^17, r=8, p=1) — memory-hard, resists brute-force
  Cipher  : AES-256-GCM — authenticated encryption (detects tampering)
  Salt    : 16 random bytes (fresh per export)
  Nonce   : 12 random bytes (fresh per export)

Bundle format (outer JSON)
──────────────────────────
  {
    "v": 1,
    "exported_at": "<ISO 8601 UTC>",
    "algo": "aes-256-gcm",
    "kdf": "scrypt",
    "kdf_n": 131072,
    "kdf_r": 8,
    "kdf_p": 1,
    "salt": "<base64>",
    "nonce": "<base64>",
    "ciphertext": "<base64>"    // AES-GCM(plaintext_json, key, nonce)
  }

Plaintext JSON (inside the ciphertext)
───────────────────────────────────────
  {
    "participants": [
      {
        "id": "<uuid>",
        "name": "<str or null>",
        "notes": "<str or null>",
        "ps_type": "<SuperpowerType or null>",
        "ps_confidence": <float or null>,
        "ps_reasoning": "<str or null>",
        "ps_state": "active|pending",
        "exported_at": "<ISO 8601 UTC>"
      },
      ...
    ]
  }

Usage
─────
    from backend.team_sync import TeamSync, ParticipantRecord

    records = [
        ParticipantRecord(
            id="abc123", name="Alice", notes="CTO at Acme",
            ps_type="Inquisitor", ps_confidence=0.82,
            ps_reasoning="Heavy use of clarifying questions",
            ps_state="active",
        )
    ]
    bundle = TeamSync.export_participants(records, passphrase="secret")
    # share 'bundle' with teammate

    imported = TeamSync.import_participants(bundle, passphrase="secret")
    for p in imported:
        print(p.name, p.ps_type)
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KDF / cipher constants
# ---------------------------------------------------------------------------

_ALGO = "aes-256-gcm"
_KDF = "scrypt"
_SCRYPT_N = 131_072   # 2^17 — OWASP-recommended minimum (2024)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_NONCE_BYTES = 12
_KEY_BYTES = 32       # 256-bit key
_BUNDLE_VERSION = 1


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ParticipantRecord:
    """
    A portable snapshot of a participant profile for Team Intelligence sync.

    Only the fields relevant to coaching are exported (no session transcripts,
    no user IDs, no internal database IDs beyond what the receiver needs to
    match against their own records).
    """
    id: str
    name: str | None
    notes: str | None
    ps_type: str | None          # SuperpowerType value or None
    ps_confidence: float | None
    ps_reasoning: str | None
    ps_state: str                # "active" | "pending"


# ---------------------------------------------------------------------------
# TeamSync
# ---------------------------------------------------------------------------

class TeamSync:
    """
    Static helpers for exporting and importing participant profiles.

    All encryption / decryption logic lives here.  The caller is responsible
    for persisting the bundle string and for merging imported records into
    the local database.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def export_participants(
        records: list[ParticipantRecord],
        passphrase: str,
    ) -> str:
        """
        Serialize *records* to an AES-256-GCM encrypted JSON bundle.

        Parameters
        ----------
        records:
            Participant profiles to export.
        passphrase:
            User-supplied passphrase used to derive the encryption key.

        Returns
        -------
        str
            JSON string containing the encrypted bundle.

        Raises
        ------
        ValueError
            If *passphrase* is empty.
        """
        if not passphrase:
            raise ValueError("passphrase must not be empty")

        plaintext = _build_plaintext(records)
        salt = os.urandom(_SALT_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        key = _derive_key(passphrase, salt)
        ciphertext = _aes_gcm_encrypt(plaintext, key, nonce)

        bundle: dict[str, Any] = {
            "v": _BUNDLE_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "algo": _ALGO,
            "kdf": _KDF,
            "kdf_n": _SCRYPT_N,
            "kdf_r": _SCRYPT_R,
            "kdf_p": _SCRYPT_P,
            "salt": _b64(salt),
            "nonce": _b64(nonce),
            "ciphertext": _b64(ciphertext),
        }
        logger.info(
            "TeamSync: exported %d participant records", len(records)
        )
        return json.dumps(bundle)

    @staticmethod
    def import_participants(
        bundle_json: str,
        passphrase: str,
    ) -> list[ParticipantRecord]:
        """
        Decrypt and parse an encrypted bundle produced by ``export_participants``.

        Parameters
        ----------
        bundle_json:
            JSON string produced by ``export_participants``.
        passphrase:
            Passphrase used when the bundle was created.

        Returns
        -------
        list[ParticipantRecord]
            Parsed participant records.

        Raises
        ------
        ValueError
            If *passphrase* is empty, the bundle is malformed, or decryption
            fails (wrong passphrase or tampered data).
        """
        if not passphrase:
            raise ValueError("passphrase must not be empty")

        try:
            bundle = json.loads(bundle_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"bundle is not valid JSON: {exc}") from exc

        _validate_bundle_schema(bundle)

        salt = _unb64(bundle["salt"])
        nonce = _unb64(bundle["nonce"])
        ciphertext = _unb64(bundle["ciphertext"])

        # Use kdf params from the bundle so future param upgrades stay compatible
        kdf_n = int(bundle.get("kdf_n", _SCRYPT_N))
        kdf_r = int(bundle.get("kdf_r", _SCRYPT_R))
        kdf_p = int(bundle.get("kdf_p", _SCRYPT_P))

        key = _derive_key(passphrase, salt, n=kdf_n, r=kdf_r, p=kdf_p)

        try:
            plaintext = _aes_gcm_decrypt(ciphertext, key, nonce)
        except Exception as exc:
            raise ValueError(
                "decryption failed — wrong passphrase or tampered data"
            ) from exc

        try:
            inner = json.loads(plaintext)
        except json.JSONDecodeError as exc:
            raise ValueError(f"decrypted payload is not valid JSON: {exc}") from exc

        records = _parse_plaintext(inner)
        logger.info(
            "TeamSync: imported %d participant records", len(records)
        )
        return records


# ---------------------------------------------------------------------------
# Internals — serialisation
# ---------------------------------------------------------------------------

def _build_plaintext(records: list[ParticipantRecord]) -> bytes:
    """Serialise records to UTF-8 JSON bytes."""
    payload = {
        "participants": [asdict(r) for r in records]
    }
    return json.dumps(payload).encode()


def _parse_plaintext(inner: dict) -> list[ParticipantRecord]:
    """
    Parse the decrypted inner JSON into ParticipantRecord objects.

    Raises ValueError on schema problems.
    """
    if not isinstance(inner, dict) or "participants" not in inner:
        raise ValueError("decrypted payload missing 'participants' key")

    raw_list = inner["participants"]
    if not isinstance(raw_list, list):
        raise ValueError("'participants' must be a list")

    records: list[ParticipantRecord] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValueError(f"participant[{i}] is not an object")
        try:
            records.append(ParticipantRecord(
                id=str(item["id"]),
                name=item.get("name"),
                notes=item.get("notes"),
                ps_type=item.get("ps_type"),
                ps_confidence=_opt_float(item.get("ps_confidence")),
                ps_reasoning=item.get("ps_reasoning"),
                ps_state=str(item.get("ps_state", "pending")),
            ))
        except KeyError as exc:
            raise ValueError(
                f"participant[{i}] missing required field: {exc}"
            ) from exc
    return records


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Internals — bundle validation
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {"v", "algo", "kdf", "salt", "nonce", "ciphertext"}


def _validate_bundle_schema(bundle: Any) -> None:
    """Raise ValueError if *bundle* is missing required top-level fields."""
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a JSON object")

    missing = _REQUIRED_FIELDS - bundle.keys()
    if missing:
        raise ValueError(f"bundle missing fields: {sorted(missing)}")

    if bundle.get("v") != _BUNDLE_VERSION:
        raise ValueError(
            f"unsupported bundle version: {bundle.get('v')!r} "
            f"(expected {_BUNDLE_VERSION})"
        )

    if bundle.get("algo") != _ALGO:
        raise ValueError(
            f"unsupported algorithm: {bundle.get('algo')!r} "
            f"(expected '{_ALGO}')"
        )

    if bundle.get("kdf") != _KDF:
        raise ValueError(
            f"unsupported KDF: {bundle.get('kdf')!r} "
            f"(expected '{_KDF}')"
        )


# ---------------------------------------------------------------------------
# Internals — crypto
# ---------------------------------------------------------------------------

def _derive_key(
    passphrase: str,
    salt: bytes,
    *,
    n: int = _SCRYPT_N,
    r: int = _SCRYPT_R,
    p: int = _SCRYPT_P,
) -> bytes:
    """Derive a 256-bit key from *passphrase* + *salt* using scrypt."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.backends import default_backend

    kdf = Scrypt(salt=salt, length=_KEY_BYTES, n=n, r=r, p=p,
                 backend=default_backend())
    return kdf.derive(passphrase.encode())


def _aes_gcm_encrypt(plaintext: bytes, key: bytes, nonce: bytes) -> bytes:
    """AES-256-GCM encrypt *plaintext*. Returns ciphertext + 16-byte auth tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).encrypt(nonce, plaintext, associated_data=None)


def _aes_gcm_decrypt(ciphertext: bytes, key: bytes, nonce: bytes) -> bytes:
    """AES-256-GCM decrypt. Raises InvalidTag on auth failure (wrong key/tampered)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)


# ---------------------------------------------------------------------------
# Internals — base64 helpers
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _unb64(s: str) -> bytes:
    try:
        return base64.b64decode(s)
    except Exception as exc:
        raise ValueError(f"invalid base64 data: {exc}") from exc
