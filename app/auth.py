import bcrypt
from fastapi import HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_session
from app.models import ApiKey

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    key: str | None = Security(api_key_header),
    db: AsyncSession = Depends(get_session),
) -> ApiKey:
    if not key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    result = await db.execute(select(ApiKey))
    for row in result.scalars():
        if bcrypt.checkpw(key.encode(), row.key_hash.encode()):
            return row
    raise HTTPException(status_code=401, detail="Invalid API key")
