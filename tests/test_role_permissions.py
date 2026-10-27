from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import UserRole
from app.services import role_service


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(role_service.cache_service, "get_json", AsyncMock(return_value=None))
    monkeypatch.setattr(role_service.cache_service, "set_json", AsyncMock())


def db_returning(rows: list[str]):
    result = MagicMock()
    result.all.return_value = rows
    db = MagicMock()
    db.scalars = AsyncMock(return_value=result)
    return db


async def test_role_without_saved_rows_uses_defaults():
    perms = await role_service.get_role_permissions(db_returning([]), UserRole.seo_specialist)
    assert perms == role_service.DEFAULT_ROLE_PERMISSIONS[UserRole.seo_specialist]


async def test_saved_rows_override_defaults():
    db = db_returning(["__customized__", "posts:view"])
    perms = await role_service.get_role_permissions(db, UserRole.seo_specialist)
    assert perms == ["posts:view"]


async def test_revoking_every_permission_is_not_replaced_by_defaults():
    db = db_returning(["__customized__"])
    perms = await role_service.get_role_permissions(db, UserRole.technical_writer)
    assert perms == []


async def test_has_permission_honours_wildcard_and_missing_permission():
    user = MagicMock(role=UserRole.super_admin)
    assert await role_service.has_permission(MagicMock(), user, "anything:at_all")

    writer = MagicMock(role=UserRole.technical_writer)
    db = db_returning(["__customized__", "posts:manage"])
    assert await role_service.has_permission(db, writer, "posts:manage")
    assert not await role_service.has_permission(db, writer, "posts:publish")
