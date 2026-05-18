"""
Encrypted key vault.

Stores customer LLM provider API keys (Google, OpenAI, Anthropic)
encrypted at rest using Fernet symmetric encryption.

The encryption key is derived from APP_SECRET_KEY in .env.
Even if someone dumps the database, they cannot read the API keys
without the encryption key from the server environment.

How it works:
  1. Customer provides their Google/OpenAI/Anthropic key at signup
  2. We encrypt it with Fernet before storing in DB
  3. On every request, we decrypt it in memory and use it for that request
  4. The decrypted key never touches disk
"""

import base64
import hashlib
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from datetime import datetime, timezone
import uuid

from logger.models import Base
from gateway.config import get_settings


# ---------------------------------------------------------------------------
# Derive a stable Fernet key from APP_SECRET_KEY
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    """
    Derive a Fernet encryption key from APP_SECRET_KEY.
    Fernet requires exactly 32 url-safe base64-encoded bytes.
    We use SHA-256 of the secret key to get consistent 32 bytes.
    """
    settings = get_settings()
    raw = settings.app_secret_key.encode()
    key_bytes = hashlib.sha256(raw).digest()         # 32 bytes
    fernet_key = base64.urlsafe_b64encode(key_bytes) # Fernet-compatible
    return Fernet(fernet_key)


def encrypt_key(raw_api_key: str) -> str:
    """Encrypt a provider API key for storage."""
    f = _get_fernet()
    return f.encrypt(raw_api_key.encode()).decode()


def decrypt_key(encrypted_key: str) -> str:
    """Decrypt a stored provider API key for use in a request."""
    f = _get_fernet()
    return f.decrypt(encrypted_key.encode()).decode()


# ---------------------------------------------------------------------------
# DB model
# ---------------------------------------------------------------------------

class ProviderKey(Base):
    __tablename__ = "provider_keys"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tenant_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    provider      = Column(String(32), nullable=False)    # "google" | "openai" | "anthropic"
    encrypted_key = Column(Text, nullable=False)          # encrypted API key
    key_prefix    = Column(String(16), nullable=True)     # first 8 chars for display
    is_active     = Column(Boolean, default=True)
    is_valid      = Column(Boolean, default=True)         # set False if key returns 401


# ---------------------------------------------------------------------------
# Key vault service
# ---------------------------------------------------------------------------

class KeyVault:

    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def store_key(
        self,
        tenant_id: str,
        provider: str,
        raw_api_key: str,
    ) -> ProviderKey:
        """Store an encrypted provider key for a tenant."""
        encrypted = encrypt_key(raw_api_key)
        prefix = raw_api_key[:8] + "..."

        # Upsert — replace existing key for same provider
        async with self.session_factory() as session:
            # Check if key exists for this provider
            result = await session.execute(
                select(ProviderKey).where(
                    ProviderKey.tenant_id == uuid.UUID(tenant_id),
                    ProviderKey.provider  == provider,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.encrypted_key = encrypted
                existing.key_prefix    = prefix
                existing.is_active     = True
                existing.is_valid      = True
                existing.updated_at    = datetime.now(timezone.utc)
                await session.commit()
                return existing
            else:
                key_record = ProviderKey(
                    tenant_id     = uuid.UUID(tenant_id),
                    provider      = provider,
                    encrypted_key = encrypted,
                    key_prefix    = prefix,
                )
                session.add(key_record)
                await session.commit()
                await session.refresh(key_record)
                return key_record

    async def get_key(self, tenant_id: str, provider: str) -> str | None:
        """
        Get decrypted provider API key for a tenant.
        Returns None if no key stored for this provider.
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(ProviderKey).where(
                    ProviderKey.tenant_id == uuid.UUID(tenant_id),
                    ProviderKey.provider  == provider,
                    ProviderKey.is_active == True,
                    ProviderKey.is_valid  == True,
                )
            )
            record = result.scalar_one_or_none()

        if not record:
            return None
        return decrypt_key(record.encrypted_key)

    async def get_available_providers(self, tenant_id: str) -> list[str]:
        """
        Return list of providers this tenant has valid keys for.
        Used by the router to know which models are available.
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(ProviderKey.provider).where(
                    ProviderKey.tenant_id == uuid.UUID(tenant_id),
                    ProviderKey.is_active == True,
                    ProviderKey.is_valid  == True,
                )
            )
            return [row[0] for row in result.fetchall()]

    async def list_keys(self, tenant_id: str) -> list[dict]:
        """List stored keys for a tenant — never returns decrypted key."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(ProviderKey).where(
                    ProviderKey.tenant_id == uuid.UUID(tenant_id),
                )
            )
            records = result.scalars().all()
        return [
            {
                "provider":   r.provider,
                "key_prefix": r.key_prefix,
                "is_active":  r.is_active,
                "is_valid":   r.is_valid,
                "added_at":   r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in records
        ]

    async def mark_key_invalid(self, tenant_id: str, provider: str):
        """
        Mark a key as invalid after a 401 response.
        Prevents hammering a bad key on every request.
        """
        async with self.session_factory() as session:
            await session.execute(
                text("""
                    UPDATE provider_keys
                    SET is_valid = false, updated_at = NOW()
                    WHERE tenant_id = :tid AND provider = :prov
                """),
                {"tid": tenant_id, "prov": provider}
            )
            await session.commit()

    async def delete_key(self, tenant_id: str, provider: str) -> bool:
        """Delete a provider key for a tenant."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(ProviderKey).where(
                    ProviderKey.tenant_id == uuid.UUID(tenant_id),
                    ProviderKey.provider  == provider,
                )
            )
            record = result.scalar_one_or_none()
            if not record:
                return False
            await session.delete(record)
            await session.commit()
        return True
