from sqlalchemy import Enum, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.user import UserRole


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (PrimaryKeyConstraint("role", "permission"),)

    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    permission: Mapped[str] = mapped_column(String(100))
