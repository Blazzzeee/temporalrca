import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .database import get_session
from .models import Agent, Host


def credential_hash(token: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).digest()


def new_credential() -> str:
    return "trca_agent_" + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: UUID
    host_id: UUID


async def authenticated_agent(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AgentIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "agent bearer credential required")
    digest = credential_hash(authorization[7:], settings.credential_pepper)
    row = (await session.execute(
        select(Agent.id, Host.id).join(Host, Host.agent_id == Agent.id).where(
            Agent.credential_hash == digest, Agent.active.is_(True)
        )
    )).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid agent credential")
    return AgentIdentity(row[0], row[1])


def require_scope(expected_token: str):
    async def dependency(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "scoped bearer credential required")
    return dependency


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
