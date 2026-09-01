from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import OperationLog


async def log_operation(
    db: AsyncSession,
    user_id: int | None,
    operation: str,
    details: str | None = None,
    request: Request | None = None,
) -> None:
    try:
        ip_address = request.client.host if request and request.client else None
        db.add(OperationLog(user_id=user_id, operation=operation, details=details, ip_address=ip_address))
        await db.commit()
    except Exception:
        pass
