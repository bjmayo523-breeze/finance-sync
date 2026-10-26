"""Encrypted secret storage.

Secrets (SimpleFIN access URL, Google service-account key) are encrypted with
Fernet (AES-128-CBC + HMAC-SHA256) into data/secrets.vault. The 44-char Fernet
key is stored in the OS keyring -- Windows Credential Manager on Windows --
which is protected by your Windows login.

Why not put the secrets straight into Credential Manager? Windows caps a
credential at ~1,280 characters and a Google key file is ~2,300.

Consequence: copying the project folder (backups, cloud sync) does not leak
secrets, because the key never lives in the folder. Anything running as your
Windows user can still read both -- that is the boundary for any user-level
secret store.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from finance_sync import paths

SERVICE = "finance-sync"
KEY_NAME = "vault-key"

SIMPLEFIN_ACCESS_URL = "simplefin_access_url"
GOOGLE_SERVICE_ACCOUNT = "google_service_account"


class VaultError(RuntimeError):
    pass


class KeyStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, value: str) -> None: ...
    def delete(self) -> None: ...
    def describe(self) -> str: ...


class KeyringStore:
    """Fernet key in the OS keyring (Windows Credential Manager)."""

    def __init__(self) -> None:
        import keyring  # imported lazily so tests can avoid the OS backend

        self._kr = keyring

    def _check_backend(self) -> None:
        backend = self._kr.get_keyring()
        mod, name = type(backend).__module__, type(backend).__name__
        if "fail" in mod or "Plaintext" in name or "null" in mod:
            raise VaultError(
                f"No secure OS keyring available (got {mod}.{name}). On Windows this "
                "should be Windows Credential Manager; run as your own user, not SYSTEM."
            )

    def get(self) -> str | None:
        self._check_backend()
        return self._kr.get_password(SERVICE, KEY_NAME)

    def set(self, value: str) -> None:
        self._check_backend()
        self._kr.set_password(SERVICE, KEY_NAME, value)

    def delete(self) -> None:
        try:
            self._kr.delete_password(SERVICE, KEY_NAME)
        except Exception:
            pass

    def describe(self) -> str:
        b = self._kr.get_keyring()
        return f"{type(b).__module__}.{type(b).__name__}"


class MemoryKeyStore:
    """For tests only."""

    def __init__(self) -> None:
        self._v: str | None = None

    def get(self) -> str | None:
        return self._v

    def set(self, value: str) -> None:
        self._v = value

    def delete(self) -> None:
        self._v = None

    def describe(self) -> str:
        return "memory (tests)"


class Vault:
    def __init__(self, path: Path | None = None, keystore: KeyStore | None = None) -> None:
        self.path = path or paths.VAULT_FILE
        self._keystore = keystore

    @property
    def keystore(self) -> KeyStore:
        if self._keystore is None:
            self._keystore = KeyringStore()
        return self._keystore

    def _fernet(self, create: bool) -> Fernet:
        key = self.keystore.get()
        if key is None:
            if self.path.exists():
                raise VaultError(
                    "secrets.vault exists but its key is missing from Credential Manager. "
                    "Run `finance-sync forget-secrets`, then redo the setup steps."
                )
            if not create:
                raise VaultError("No secrets stored yet. Run the setup commands first.")
            key = Fernet.generate_key().decode()
            self.keystore.set(key)
        return Fernet(key.encode())

    def _load(self, create: bool = False) -> dict:
        f = self._fernet(create=create)
        if not self.path.exists():
            return {}
        try:
            return json.loads(f.decrypt(self.path.read_bytes()))
        except InvalidToken as e:
            raise VaultError(
                "secrets.vault could not be decrypted with the stored key "
                "(file corrupted or key replaced). Run `forget-secrets` and redo setup."
            ) from e

    def _save(self, data: dict) -> None:
        f = self._fernet(create=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(f.encrypt(json.dumps(data).encode()))
        os.replace(tmp, self.path)

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def require(self, name: str, hint: str) -> str:
        v = self.get(name)
        if not v:
            raise VaultError(f"Missing secret '{name}'. {hint}")
        return v

    def set(self, name: str, value: str) -> None:
        data = self._load(create=True)
        data[name] = value
        self._save(data)

    def names(self) -> list[str]:
        if not self.path.exists():
            return []
        return sorted(self._load().keys())

    def destroy(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.keystore.delete()
