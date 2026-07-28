from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import Role, User


class RoleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[Role]:
        result = await self.db.execute(select(Role).order_by(Role.is_system.desc(), Role.created_at, Role.key))
        return list(result.scalars().all())

    async def get(self, key: str) -> Role | None:
        result = await self.db.execute(select(Role).where(Role.key == key))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Role | None:
        result = await self.db.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    async def create(self, **data) -> Role:
        role = Role(**data)
        self.db.add(role)
        await self.db.commit()
        await self.db.refresh(role)
        return role

    async def update(self, role: Role, **data) -> Role:
        for key, value in data.items():
            setattr(role, key, value)
        await self.db.commit()
        await self.db.refresh(role)
        return role

    async def user_count(self, key: str) -> int:
        result = await self.db.execute(select(func.count(User.id)).where(User.role == key, User.is_deleted == 0))
        return int(result.scalar() or 0)

    async def delete(self, role: Role) -> None:
        await self.db.execute(update(User).where(User.role == role.key, User.is_deleted != 0).values(role="user"))
        await self.db.delete(role)
        await self.db.commit()
