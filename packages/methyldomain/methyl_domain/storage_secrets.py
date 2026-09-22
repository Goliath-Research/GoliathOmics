"""Resolve storage credential refs (Key Vault / encrypted file) for workers.

Preferred dumb-worker path: concrete credentials arrive in task JSON over the
gateway TLS channel. When ``contentHash`` / ``credentialName`` are present,
workers refresh a **node-local** Fernet cache under ``/var/lib/methyl`` (never
shared ``/work``). Azure Key Vault remains an optional escape hatch.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_NODE_CACHE_DIR = Path(
    os.environ.get(
        "METHYL_STORAGE_CRED_CACHE_DIR",
        "/var/lib/methyl/storage-credentials",
    )
)
DEFAULT_NODE_KEY_PATH = Path(
    os.environ.get(
        "METHYL_STORAGE_CREDENTIAL_KEY_FILE",
        "/etc/methyl/storage-credential.key",
    )
)


def _fernet_key_from_password(password: Optional[str] = None) -> bytes:
    from cryptography.fernet import Fernet  # noqa: F401 — ensure installed
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    if password is None:
        password = os.environ.get("METHYL_STORAGE_CREDENTIAL_PASSWORD", "")
        if not password:
            salt = str(Path.home()).encode()
        else:
            salt = password.encode()
    else:
        salt = password.encode()
    salt_bytes = salt[:16].ljust(16, b"0")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt_bytes,
        iterations=100000,
    )
    key = kdf.derive(b"methyl_storage_secret")
    return base64.urlsafe_b64encode(key)


def read_encrypted_secret_file(path: Path, *, password: Optional[str] = None) -> str:
    from cryptography.fernet import Fernet

    data = path.read_bytes()
    # Support JSON wrapper {"value": "..."} encrypted as whole file of ciphertext
    f = Fernet(_fernet_key_from_password(password))
    plain = f.decrypt(data).decode("utf-8")
    # Allow JSON object with nested fields for multi-field secrets
    try:
        parsed = json.loads(plain)
        if isinstance(parsed, dict) and "value" in parsed and len(parsed) == 1:
            return str(parsed["value"])
        if isinstance(parsed, dict):
            return plain  # multi-field JSON string for caller to parse
        return plain
    except json.JSONDecodeError:
        return plain


def write_encrypted_secret_file(
    path: Path,
    value: str,
    *,
    password: Optional[str] = None,
) -> None:
    from cryptography.fernet import Fernet

    path.parent.mkdir(parents=True, exist_ok=True)
    f = Fernet(_fernet_key_from_password(password))
    path.write_bytes(f.encrypt(value.encode("utf-8")))
    os.chmod(path, 0o600)


def read_key_vault_secret(vault_url: str, secret_name: str) -> str:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    secret = client.get_secret(secret_name)
    if secret.value is None:
        raise RuntimeError(f"Key Vault secret {secret_name!r} at {vault_url} has no value")
    return secret.value


def _node_cache_password() -> Optional[str]:
    """Wrap key for node-local Fernet cache (host file or env)."""
    key_path = DEFAULT_NODE_KEY_PATH
    if key_path.is_file():
        return key_path.read_text(encoding="utf-8").strip()
    return os.environ.get("METHYL_STORAGE_CREDENTIAL_PASSWORD") or None


def _cache_file_for(credential_name: str, cache_dir: Path) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in credential_name)
    return cache_dir / f"{safe}.encrypted"


def read_node_credential_cache(
    credential_name: str,
    *,
    cache_dir: Optional[Path] = None,
) -> Optional[Tuple[str, dict[str, Any]]]:
    """
    Return ``(content_hash, credentials_dict)`` from node-local cache, or None.
    """
    cache_dir = cache_dir or DEFAULT_NODE_CACHE_DIR
    path = _cache_file_for(credential_name, cache_dir)
    meta_path = path.with_suffix(".hash")
    if not path.is_file() or not meta_path.is_file():
        return None
    try:
        content_hash = meta_path.read_text(encoding="utf-8").strip()
        raw = read_encrypted_secret_file(path, password=_node_cache_password())
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return content_hash, parsed
    except Exception as exc:  # noqa: BLE001 — treat corrupt cache as miss
        logger.warning("Ignoring corrupt storage credential cache %s: %s", path, exc)
        return None


def write_node_credential_cache(
    credential_name: str,
    content_hash: str,
    credentials: Mapping[str, Any],
    *,
    cache_dir: Optional[Path] = None,
) -> Path:
    """
    Persist credentials under a node-local directory (never under ``/work``).

    Raises if ``cache_dir`` resolves under a shared work root.
    """
    cache_dir = Path(cache_dir or DEFAULT_NODE_CACHE_DIR)
    resolved = cache_dir.resolve()
    work_root = Path(os.environ.get("METHYL_WORK_ROOT", "/work")).resolve()
    try:
        resolved.relative_to(work_root)
        raise RuntimeError(
            f"Refusing to write storage credential cache under shared work root "
            f"{work_root}; use node-local path (got {resolved})"
        )
    except ValueError:
        pass  # not under /work — ok

    path = _cache_file_for(credential_name, cache_dir)
    # Strip change-token fields from encrypted body (payload is auth fields only)
    body = {
        k: v
        for k, v in credentials.items()
        if k
        not in (
            "credentialName",
            "credentialVersion",
            "contentHash",
            "content_hash",
        )
    }
    write_encrypted_secret_file(
        path, json.dumps(body, sort_keys=True), password=_node_cache_password()
    )
    meta = path.with_suffix(".hash")
    meta.write_text(str(content_hash).strip() + "\n", encoding="utf-8")
    os.chmod(meta, 0o600)
    return path


def apply_node_credential_cache(
    credentials: Mapping[str, Any],
    *,
    cache_dir: Optional[Path] = None,
) -> Mapping[str, Any]:
    """
    Compare task ``contentHash`` to node-local cache and refresh when changed.

    Prefer the task payload (gateway SoT for this claim). Cache is updated when
    the hash differs so restarts can reuse last-known secrets.
    """
    name = credentials.get("credentialName") or credentials.get("credential_name")
    content_hash = credentials.get("contentHash") or credentials.get("content_hash")
    if not name or not content_hash:
        return credentials

    cache_dir = cache_dir or DEFAULT_NODE_CACHE_DIR
    cached = read_node_credential_cache(str(name), cache_dir=cache_dir)
    if cached is None or cached[0] != str(content_hash).strip():
        try:
            write_node_credential_cache(
                str(name), str(content_hash), credentials, cache_dir=cache_dir
            )
            logger.info(
                "Updated node-local storage credential cache for %s (hash=%s)",
                name,
                content_hash,
            )
        except Exception as exc:  # noqa: BLE001 — transfers still use payload
            logger.warning(
                "Could not write node-local credential cache for %s: %s", name, exc
            )
        return credentials
    # Hash matches — prefer payload (may include fresh non-secret fields)
    return credentials


def resolve_secret_payload(credentials: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Expand vault / encrypted_file credential refs into concrete auth fields.

    Returns a new mapping suitable for building boto3 / Azure clients.
    Passes through explicit_keys / account_key / connection_string / ambient modes.
    Refreshes node-local cache when ``contentHash`` is present.
    """
    credentials = apply_node_credential_cache(credentials)
    auth = str(credentials.get("authMode") or "")
    if auth == "azure_key_vault":
        vault_url = credentials.get("vaultUrl") or credentials.get("azure_key_vault_url")
        secret_name = credentials.get("secretName") or credentials.get("azure_secret_name")
        if not vault_url or not secret_name:
            raise RuntimeError(
                "azure_key_vault credentials require vaultUrl and secretName"
            )
        raw = read_key_vault_secret(str(vault_url), str(secret_name))
        return _payload_from_vault_or_file_value(raw, credentials)
    if auth == "encrypted_file":
        path = credentials.get("path") or credentials.get("encrypted_file_path")
        if not path:
            raise RuntimeError("encrypted_file credentials require path")
        raw = read_encrypted_secret_file(Path(str(path)).expanduser())
        return _payload_from_vault_or_file_value(raw, credentials)
    return credentials


def _payload_from_vault_or_file_value(
    raw: str, credentials: Mapping[str, Any]
) -> dict[str, Any]:
    """
    Interpret secret value as JSON auth payload or a single secret string.

    JSON examples:
      {"authMode":"explicit_keys","accessKeyId":"...","secretAccessKey":"..."}
      {"authMode":"account_key","accountKey":"..."}
      {"authMode":"connection_string","connectionString":"..."}
    """
    target = credentials.get("materializeAs") or credentials.get("secretKind")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("authMode"):
        return dict(parsed)
    # Single string secret — materialize into requested shape
    if target == "account_key" or credentials.get("providerHint") == "azure_blob":
        return {"authMode": "account_key", "accountKey": raw}
    if target == "connection_string":
        return {"authMode": "connection_string", "connectionString": raw}
    # Azure connection strings often contain "https://host:port" — detect before
    # any accessKeyId:secretAccessKey colon split (otherwise first ":" wins).
    if _looks_like_azure_connection_string(raw):
        return {"authMode": "connection_string", "connectionString": raw}
    # S3 access key pair encoded as "accessKeyId:secretAccessKey"
    if target in (None, "explicit_keys", "s3") and _looks_like_s3_access_key_pair(raw):
        access, _, secret = raw.partition(":")
        return {
            "authMode": "explicit_keys",
            "accessKeyId": access,
            "secretAccessKey": secret,
        }
    if target == "explicit_keys":
        raise RuntimeError(
            "Key Vault/encrypted secret for explicit_keys must be JSON or "
            "accessKeyId:secretAccessKey"
        )
    raise RuntimeError(
        "Unable to interpret vault/encrypted secret; store JSON with authMode "
        "or accessKeyId:secretAccessKey / account key / connection string"
    )


def _looks_like_azure_connection_string(raw: str) -> bool:
    """True when a non-JSON secret is an Azure Storage connection string."""
    markers = (
        "AccountKey=",
        "AccountName=",
        "BlobEndpoint=",
        "DefaultEndpointsProtocol=",
        "SharedAccessSignature=",
        "EndpointSuffix=",
    )
    return any(marker in raw for marker in markers)


def _looks_like_s3_access_key_pair(raw: str) -> bool:
    """True for ``accessKeyId:secretAccessKey`` (not key=value / URL forms)."""
    if ":" not in raw or "=" in raw.split(":", 1)[0]:
        return False
    access, _, secret = raw.partition(":")
    if not access or not secret:
        return False
    # Reject URL-like left sides (e.g. "https")
    if access.lower() in ("http", "https") or access.endswith("/"):
        return False
    return True
