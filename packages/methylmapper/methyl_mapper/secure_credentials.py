"""
Secure credential management for API keys.

Supports:
- Azure Key Vault
- Encrypted local file storage
- Environment variables (fallback)
"""

import logging
import os
from pathlib import Path
from typing import Optional
import base64
import json
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class SecureCredentialManager:
    """
    Manages secure storage and retrieval of API keys (e.g. Grok API key).
    
    Resolution order when get_credential() is called:
    1. Explicitly provided key (e.g. from config file or --grok-api-key)
    2. Encrypted local file (~/.methyl_mapper/credentials/{name}.encrypted)
    3. Azure Key Vault (if configured)
    4. Environment variable (e.g. GROK_API_KEY)
    
    Use save_credential() to store the key locally and/or in Azure so it is
    always available without putting it in config.
    """
    
    def __init__(
        self,
        credential_name: str = "grok_api_key",
        azure_key_vault_url: Optional[str] = None,
        azure_secret_name: Optional[str] = None,
        encrypted_file_path: Optional[Path] = None,
        env_var_name: Optional[str] = None,
        methyl_mapper_home: Optional[Path] = None,
    ):
        """
        Initialize SecureCredentialManager.
        
        Args:
            credential_name: Name of the credential (for logging)
            azure_key_vault_url: Azure Key Vault URL (e.g., "https://{vault-name}.vault.azure.net/")
            azure_secret_name: Key Vault secret name override; default is derived from credential_name (hyphenated)
            encrypted_file_path: Path to encrypted credential file
            env_var_name: Environment variable name (defaults to credential_name.upper())
            methyl_mapper_home: Optional root directory (e.g. /path/.methyl_mapper) for
                default credentials under {home}/credentials/; defaults to ~/.methyl_mapper
        """
        self.credential_name = credential_name
        self.azure_key_vault_url = azure_key_vault_url or os.environ.get('AZURE_KEY_VAULT_URL')
        # Azure Key Vault secret names must use hyphens, not underscores.
        # Per-credential default only (do not use a single AZURE_SECRET_NAME for all keys).
        default_secret_name = credential_name.replace('_', '-')
        self.azure_secret_name = (
            azure_secret_name.strip() if azure_secret_name else default_secret_name
        )
        self.methyl_mapper_home = Path(methyl_mapper_home).expanduser() if methyl_mapper_home else None
        self.encrypted_file_path = encrypted_file_path or self._get_default_encrypted_path()
        self.env_var_name = env_var_name or credential_name.upper().replace('-', '_')
        self.last_resolution_error: Optional[str] = None
        
        # Lazy initialization of Azure client
        self._azure_client = None
    
    def _get_default_encrypted_path(self) -> Path:
        """Get default path for encrypted credential file."""
        root = self.methyl_mapper_home if self.methyl_mapper_home is not None else (Path.home() / ".methyl_mapper")
        cred_dir = root / "credentials"
        cred_dir.mkdir(parents=True, exist_ok=True)
        return cred_dir / f"{self.credential_name}.encrypted"
    
    def _get_azure_client(self):
        """Lazy load Azure Key Vault client."""
        if self._azure_client is not None:
            return self._azure_client
        
        if not self.azure_key_vault_url:
            return None
        
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            
            credential = DefaultAzureCredential()
            self._azure_client = SecretClient(
                vault_url=self.azure_key_vault_url,
                credential=credential
            )
            logger.info(f"✅ Connected to Azure Key Vault: {self.azure_key_vault_url}")
            return self._azure_client
        except ImportError:
            logger.warning("Azure Key Vault libraries not installed. Install with: pip install azure-identity azure-keyvault-secrets")
            return None
        except Exception as e:
            logger.warning(f"Failed to connect to Azure Key Vault: {e}")
            return None
    
    def _get_encryption_key(self, password: Optional[str] = None) -> bytes:
        """
        Generate encryption key from password.
        
        Args:
            password: Password for encryption (defaults to user-specific key)
        """
        if password is None:
            # Use a combination of username and home directory as salt
            password = os.environ.get('METHYL_MAPPER_CREDENTIAL_PASSWORD', '')
            if not password:
                # Fallback: use user home directory as salt
                salt = str(Path.home()).encode()
            else:
                salt = password.encode()
        else:
            salt = password.encode()
        
        # Use a fixed salt for consistency (in production, consider user-specific salt)
        # PBKDF2HMAC is compatible with older cryptography versions
        salt_bytes = salt[:16].ljust(16, b'0')  # Ensure 16-byte salt
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt_bytes,
            iterations=100000,
        )
        key = kdf.derive(b'methyl_mapper_secret')
        return base64.urlsafe_b64encode(key)
    
    def _encrypt_value(self, value: str, password: Optional[str] = None) -> bytes:
        """Encrypt a value."""
        key = self._get_encryption_key(password)
        f = Fernet(key)
        return f.encrypt(value.encode())
    
    def _decrypt_value(self, encrypted_value: bytes, password: Optional[str] = None) -> str:
        """Decrypt a value."""
        key = self._get_encryption_key(password)
        f = Fernet(key)
        return f.decrypt(encrypted_value).decode()
    
    def get_credential(self, explicit_key: Optional[str] = None) -> Optional[str]:
        """
        Get credential following priority order.
        
        Args:
            explicit_key: Explicitly provided key (highest priority)
            
        Returns:
            API key or None if not found
        """
        self.last_resolution_error = None
        # 1. Explicitly provided key
        if explicit_key:
            return explicit_key
        
        # 2. Encrypted local file (fastest, works offline)
        if self.encrypted_file_path and self.encrypted_file_path.exists():
            try:
                with open(self.encrypted_file_path, 'rb') as f:
                    encrypted_data = f.read()
                decrypted = self._decrypt_value(encrypted_data)
                logger.debug(f"✅ Retrieved {self.credential_name} from encrypted file")
                return decrypted
            except Exception as e:
                self.last_resolution_error = (
                    f"Failed to decrypt local credential file {self.encrypted_file_path}: {e}"
                )
                logger.warning(self.last_resolution_error)
        
        # 3. Azure Key Vault (fallback if local file not available)
        if self.azure_key_vault_url:
            try:
                client = self._get_azure_client()
                if client:
                    secret = client.get_secret(self.azure_secret_name)
                    logger.debug(f"✅ Retrieved {self.credential_name} from Azure Key Vault")
                    return secret.value
            except Exception as e:
                self.last_resolution_error = (
                    f"Azure Key Vault retrieval failed for secret '{self.azure_secret_name}' "
                    f"at '{self.azure_key_vault_url}': {e}"
                )
                logger.warning(self.last_resolution_error)
        
        # 4. Environment variable (last resort)
        env_value = os.environ.get(self.env_var_name)
        if env_value:
            logger.debug(f"✅ Retrieved {self.credential_name} from environment variable")
            return env_value

        if self.azure_key_vault_url and self.last_resolution_error is None:
            self.last_resolution_error = (
                f"No credential found in Azure Key Vault (secret '{self.azure_secret_name}' "
                f"at '{self.azure_key_vault_url}') and environment variable '{self.env_var_name}' is empty."
            )
        
        return None
    
    def save_credential(
        self,
        value: str,
        use_azure: bool = False,
        use_encrypted_file: bool = True,
        password: Optional[str] = None
    ) -> bool:
        """
        Save credential securely.
        
        Args:
            value: The credential value to save
            use_azure: Whether to save to Azure Key Vault
            use_encrypted_file: Whether to save to encrypted local file
            password: Password for encryption (optional, uses env var or default)
            
        Returns:
            True if saved successfully
        """
        success = False
        
        # Save to Azure Key Vault
        if use_azure and self.azure_key_vault_url:
            try:
                client = self._get_azure_client()
                if client:
                    client.set_secret(self.azure_secret_name, value)
                    logger.info(f"✅ Saved {self.credential_name} to Azure Key Vault")
                    success = True
            except Exception as e:
                logger.error(f"Failed to save to Azure Key Vault: {e}")
        
        # Save to encrypted local file
        if use_encrypted_file:
            try:
                encrypted_data = self._encrypt_value(value, password)
                self.encrypted_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Set secure permissions (owner read/write only)
                with open(self.encrypted_file_path, 'wb') as f:
                    f.write(encrypted_data)
                os.chmod(self.encrypted_file_path, 0o600)  # rw-------
                
                logger.info(f"✅ Saved {self.credential_name} to encrypted file: {self.encrypted_file_path}")
                success = True
            except Exception as e:
                logger.error(f"Failed to save encrypted credential file: {e}")
        
        return success


def persist_secret_if_changed(
    manager: SecureCredentialManager,
    value: str,
    *,
    use_azure: bool,
) -> bool:
    """
    If value is non-empty and differs from stored secret (file/vault/env chain without explicit),
    save to encrypted local file and optionally Azure Key Vault.
    """
    if not value or not str(value).strip():
        return False
    value = str(value).strip()
    existing = manager.get_credential(explicit_key=None)
    if existing == value:
        logger.debug(f"No persist needed for {manager.credential_name} (unchanged)")
        return False
    return manager.save_credential(value, use_azure=use_azure, use_encrypted_file=True)

