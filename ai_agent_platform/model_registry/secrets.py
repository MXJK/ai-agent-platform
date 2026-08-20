"""Write-only secret references backed by bounded secret stores."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


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


class EncryptedFileSecretStore:
    """Persists encrypted secrets for the single-node Compose deployment.

    The encrypted payload and its randomly generated host key live in the App's
    private persistent volume with owner-only permissions. This deliberately
    avoids dotenv/API-key configuration while keeping PostgreSQL limited to
    opaque references. Multi-node deployments should replace this backend with
    an external KMS or secret manager.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._key_path = self._path.with_name(f"{self._path.name}.key")
        self._lock = Lock()
        self._fernet = Fernet(self._load_or_create_key())

    def get(self, secret_ref: str) -> str | None:
        if secret_ref.startswith("env:"):
            return os.getenv(secret_ref.removeprefix("env:")) or None
        with self._lock:
            encrypted = self._read_values().get(secret_ref)
            if encrypted is None:
                return None
            try:
                return self._fernet.decrypt(encrypted.encode("ascii")).decode(
                    "utf-8"
                )
            except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
                raise SecretStoreError("failed to decrypt the stored secret") from exc

    def set(self, secret_ref: str, value: str) -> None:
        if not value.strip():
            raise ValueError("API key must not be blank")
        with self._lock:
            values = self._read_values()
            values[secret_ref] = self._fernet.encrypt(
                value.encode("utf-8")
            ).decode("ascii")
            self._write_values(values)

    def delete(self, secret_ref: str) -> None:
        if secret_ref.startswith("env:"):
            return
        with self._lock:
            values = self._read_values()
            if secret_ref not in values:
                return
            values.pop(secret_ref)
            self._write_values(values)

    def _load_or_create_key(self) -> bytes:
        self._ensure_parent()
        try:
            key = self._key_path.read_bytes().strip()
        except FileNotFoundError:
            if self._path.exists():
                raise SecretStoreError(
                    "encrypted secrets exist but their encryption key is missing"
                )
            key = Fernet.generate_key()
            try:
                descriptor = os.open(
                    self._key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                key = self._key_path.read_bytes().strip()
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise SecretStoreError("stored secret encryption key is invalid") from exc
        try:
            os.chmod(self._key_path, 0o600)
        except OSError as exc:
            raise SecretStoreError(
                "failed to secure the secret encryption key"
            ) from exc
        return key

    def _read_values(self) -> dict[str, str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretStoreError("failed to read the encrypted secret store") from exc
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw.items()
        ):
            raise SecretStoreError("encrypted secret store has an invalid format")
        return raw

    def _write_values(self, values: dict[str, str]) -> None:
        self._ensure_parent()
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                os.chmod(temporary_path, 0o600)
                json.dump(values, stream, ensure_ascii=True, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
            os.chmod(self._path, 0o600)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise SecretStoreError("failed to persist the encrypted secret store") from exc

    def _ensure_parent(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise SecretStoreError("failed to create the secret store directory") from exc


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError(
            "keyring is required for frontend API-key storage; install project dependencies"
        ) from exc
    return keyring
