"""Write-only secret references backed by the operating-system keyring."""

from __future__ import annotations

import os
from threading import Lock
from typing import Protocol


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, secret_ref: str) -> str | None: ...

    def set(self, secret_ref: str, value: str) -> None: ...

    def delete(self, secret_ref: str) -> None: ...


class KeyringSecretStore:
    """Uses macOS Keychain or the host keyring selected by ``keyring``."""

    def __init__(self, *, service_name: str = "ai-agent-platform") -> None:
        self._service_name = service_name

    def get(self, secret_ref: str) -> str | None:
        if secret_ref.startswith("env:"):
            return os.getenv(secret_ref.removeprefix("env:")) or None
        keyring = _keyring()
        try:
            return keyring.get_password(self._service_name, secret_ref)
        except Exception as exc:
            raise SecretStoreError("failed to read the operating-system keyring") from exc

    def set(self, secret_ref: str, value: str) -> None:
        if not value.strip():
            raise ValueError("API key must not be blank")
        keyring = _keyring()
        try:
            keyring.set_password(self._service_name, secret_ref, value)
        except Exception as exc:
            raise SecretStoreError("failed to write the operating-system keyring") from exc

    def delete(self, secret_ref: str) -> None:
        if secret_ref.startswith("env:"):
            return
        keyring = _keyring()
        try:
            keyring.delete_password(self._service_name, secret_ref)
        except keyring.errors.PasswordDeleteError:
            return
        except Exception as exc:
            raise SecretStoreError("failed to delete the operating-system keyring entry") from exc


class InMemorySecretStore:
    """Deterministic test/local fallback selected explicitly through settings."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = Lock()

    def get(self, secret_ref: str) -> str | None:
        if secret_ref.startswith("env:"):
            return os.getenv(secret_ref.removeprefix("env:")) or None
        with self._lock:
            return self._values.get(secret_ref)

    def set(self, secret_ref: str, value: str) -> None:
        if not value.strip():
            raise ValueError("API key must not be blank")
        with self._lock:
            self._values[secret_ref] = value

    def delete(self, secret_ref: str) -> None:
        if secret_ref.startswith("env:"):
            return
        with self._lock:
            self._values.pop(secret_ref, None)


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError(
            "keyring is required for frontend API-key storage; install project dependencies"
        ) from exc
    return keyring
