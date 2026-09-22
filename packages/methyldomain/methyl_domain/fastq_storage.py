"""
Structured FASTQ storage locations for Portal JSON and download workers.

Instance-level ``FastqStorageDefaults`` plus per-sample ``prefix`` merge into
``FastqSourceLocation`` on each download task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

_SECRET_JSON_SCHEMA = {"writeOnly": True}

StorageScope = Literal["lab_ingress", "archive", "shared"]


class StorageChangeTokens(BaseModel):
    """Optional schedule-time expand tokens for credential rotation / RBAC scope."""

    model_config = ConfigDict(extra="forbid")

    scope: StorageScope | None = Field(
        default=None,
        description="Admin RBAC hint: lab_ingress vs archive vs shared.",
    )
    credentialName: str | None = Field(
        default=None,
        description="cfg.credential name bound at expand time.",
    )
    credentialVersion: str | None = Field(
        default=None,
        description="Published credential version bound into task JSON.",
    )
    contentHash: str | None = Field(
        default=None,
        description="cfg.credential.content_hash; workers refresh node-local cache when this changes.",
    )


class CredentialChangeTokens(BaseModel):
    """Change tokens nested on credentials by assemble_storage_location."""

    model_config = ConfigDict(extra="forbid")

    credentialName: str | None = None
    credentialVersion: str | None = None
    contentHash: str | None = None


def reveal_secrets(value: Any) -> Any:
    """Recursively expand SecretStr for workflow context_json / worker task payloads."""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: reveal_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [reveal_secrets(item) for item in value]
    return value


def dump_storage_model(model: BaseModel) -> dict[str, Any]:
    """Serialize storage models with credential secrets visible to workers."""
    return reveal_secrets(model.model_dump(mode="python", exclude_none=True))


def normalize_sample_prefix(prefix: str) -> str:
    """Normalize a per-sample folder prefix (trailing slash, no leading slash)."""
    cleaned = str(prefix).strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def resolve_sample_storage_prefix(
    *,
    sample_id: str,
    prefix_base: str | None = None,
    explicit_prefix: str | None = None,
) -> str:
    """Resolve per-sample object prefix, optionally under a platform base path."""
    if explicit_prefix:
        return normalize_sample_prefix(explicit_prefix)
    if prefix_base:
        base = str(prefix_base).strip().strip("/")
        sid = str(sample_id).strip().strip("/")
        if base:
            return normalize_sample_prefix(f"{base}/{sid}")
    return normalize_sample_prefix(sample_id)


def _token_kwargs(model: StorageChangeTokens) -> dict[str, Any]:
    return {
        "scope": model.scope,
        "credentialName": model.credentialName,
        "credentialVersion": model.credentialVersion,
        "contentHash": model.contentHash,
    }


class S3ExplicitKeysCredentials(CredentialChangeTokens):
    authMode: Literal["explicit_keys"] = "explicit_keys"
    accessKeyId: str
    secretAccessKey: SecretStr = Field(json_schema_extra=_SECRET_JSON_SCHEMA)
    sessionToken: SecretStr | None = Field(default=None, json_schema_extra=_SECRET_JSON_SCHEMA)


class S3InstanceProfileCredentials(CredentialChangeTokens):
    authMode: Literal["instance_profile"] = "instance_profile"


class AzureKeyVaultCredentials(CredentialChangeTokens):
    """Credential *reference* resolved on the worker via Managed Identity (optional escape hatch)."""

    authMode: Literal["azure_key_vault"] = "azure_key_vault"
    vaultUrl: str = Field(description="Azure Key Vault URL (https://….vault.azure.net/).")
    secretName: str = Field(description="Secret name holding JSON or accessKeyId:secret.")
    materializeAs: Literal["explicit_keys", "account_key", "connection_string"] | None = Field(
        default=None,
        description="Optional hint for how to interpret a plain-string secret value.",
    )


class EncryptedFileCredentials(CredentialChangeTokens):
    """Credential *reference* to a Fernet-encrypted local file (node-local / air-gapped)."""

    authMode: Literal["encrypted_file"] = "encrypted_file"
    path: str = Field(description="Path to encrypted credential file (owner rw only).")
    materializeAs: Literal["explicit_keys", "account_key", "connection_string"] | None = Field(
        default=None,
        description="Optional hint for how to interpret a plain-string secret value.",
    )


S3Credentials = Annotated[
    Union[
        S3ExplicitKeysCredentials,
        S3InstanceProfileCredentials,
        AzureKeyVaultCredentials,
        EncryptedFileCredentials,
    ],
    Field(discriminator="authMode"),
]


class AzureAccountKeyCredentials(CredentialChangeTokens):
    authMode: Literal["account_key"] = "account_key"
    accountKey: SecretStr = Field(json_schema_extra=_SECRET_JSON_SCHEMA)


class AzureConnectionStringCredentials(CredentialChangeTokens):
    authMode: Literal["connection_string"] = "connection_string"
    connectionString: SecretStr = Field(json_schema_extra=_SECRET_JSON_SCHEMA)


class AzureDefaultCredentialCredentials(CredentialChangeTokens):
    authMode: Literal["default_credential"] = "default_credential"


AzureCredentials = Annotated[
    Union[
        AzureAccountKeyCredentials,
        AzureConnectionStringCredentials,
        AzureDefaultCredentialCredentials,
        AzureKeyVaultCredentials,
        EncryptedFileCredentials,
    ],
    Field(discriminator="authMode"),
]


class FileFastqStorageDefaults(StorageChangeTokens):
    type: Literal["file"] = "file"
    basePath: str


class S3FastqStorageDefaults(StorageChangeTokens):
    type: Literal["s3"] = "s3"
    bucket: str
    region: str | None = None
    endpointUrl: str | None = None
    prefixBase: str | None = None
    credentials: S3Credentials


class AzureFastqStorageDefaults(StorageChangeTokens):
    type: Literal["azure_blob"] = "azure_blob"
    account: str
    container: str
    credentials: AzureCredentials


FastqStorageDefaults = Annotated[
    Union[FileFastqStorageDefaults, S3FastqStorageDefaults, AzureFastqStorageDefaults],
    Field(discriminator="type"),
]


class FileFastqSource(StorageChangeTokens):
    type: Literal["file"] = "file"
    basePath: str
    prefix: str = ""

    @model_validator(mode="after")
    def _normalize_prefix(self) -> FileFastqSource:
        if self.prefix:
            object.__setattr__(self, "prefix", normalize_sample_prefix(self.prefix))
        return self


class S3FastqSource(StorageChangeTokens):
    type: Literal["s3"] = "s3"
    bucket: str
    prefix: str
    region: str | None = None
    endpointUrl: str | None = None
    credentials: S3Credentials

    @model_validator(mode="after")
    def _normalize_prefix(self) -> S3FastqSource:
        object.__setattr__(self, "prefix", normalize_sample_prefix(self.prefix))
        return self


class AzureFastqSource(StorageChangeTokens):
    type: Literal["azure_blob"] = "azure_blob"
    account: str
    container: str
    prefix: str
    credentials: AzureCredentials

    @model_validator(mode="after")
    def _normalize_prefix(self) -> AzureFastqSource:
        object.__setattr__(self, "prefix", normalize_sample_prefix(self.prefix))
        return self


FastqSourceLocation = Annotated[
    Union[FileFastqSource, S3FastqSource, AzureFastqSource],
    Field(discriminator="type"),
]


def merge_fastq_source(defaults: FastqStorageDefaults, prefix: str) -> FastqSourceLocation:
    """Materialize per-sample source from instance defaults and sample prefix."""
    norm = normalize_sample_prefix(prefix)
    tokens = _token_kwargs(defaults)
    if isinstance(defaults, FileFastqStorageDefaults):
        return FileFastqSource(basePath=defaults.basePath, prefix=norm, **tokens)
    if isinstance(defaults, S3FastqStorageDefaults):
        return S3FastqSource(
            bucket=defaults.bucket,
            prefix=norm,
            region=defaults.region,
            endpointUrl=defaults.endpointUrl,
            credentials=defaults.credentials,
            **tokens,
        )
    if isinstance(defaults, AzureFastqStorageDefaults):
        return AzureFastqSource(
            account=defaults.account,
            container=defaults.container,
            prefix=norm,
            credentials=defaults.credentials,
            **tokens,
        )
    raise TypeError(f"unsupported fastq storage defaults: {type(defaults)!r}")


def resolve_file_local_root(source: FileFastqSource) -> Path:
    base = Path(source.basePath).expanduser()
    if not source.prefix:
        return base
    return base / source.prefix.rstrip("/")
