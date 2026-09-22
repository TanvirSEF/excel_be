from pydantic import BaseModel, Field
from app.models import UserRole
from app.schemas.common import RequestModel


class PermissionItemOut(BaseModel):
    id: str
    name: str
    description: str


class PermissionGroupOut(BaseModel):
    id: str
    title: str
    description: str
    permissions: list[PermissionItemOut]


class RoleOut(BaseModel):
    role: UserRole
    name: str
    description: str
    member_count: int
    is_system: bool = True
    is_editable: bool = True
    permissions: list[str]


class RolePermissionsUpdate(RequestModel):
    permissions: list[str] = Field(
        ..., description="Full list of permission identifiers to set for this role"
    )
