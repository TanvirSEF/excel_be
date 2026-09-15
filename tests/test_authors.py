import uuid
from datetime import datetime, timezone

from app.models.user import User
from app.schemas.author import AuthorOut


def test_author_out_from_attributes():
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    user = User(
        id=user_id,
        name="Excel Expert",
        email="expert@excelinsider.com",
        password_hash="secret",
        avatar_url="https://example.com/avatar.jpg",
        bio="Spreadsheet wizard",
        created_at=now,
    )

    author = AuthorOut.model_validate(user)
    assert author.id == user_id
    assert author.name == "Excel Expert"
    assert author.joined_at == now
    assert author.avatar_url == "https://example.com/avatar.jpg"
    assert author.bio == "Spreadsheet wizard"
    assert author.post_count == 0


def test_author_out_direct_instantiation():
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    author = AuthorOut(
        id=user_id,
        name="Excel Expert",
        avatar_url=None,
        bio=None,
        joined_at=now,
        post_count=12,
    )
    assert author.id == user_id
    assert author.name == "Excel Expert"
    assert author.joined_at == now
    assert author.post_count == 12
    data = author.model_dump(mode="json")
    assert data["id"] == str(user_id)
    assert data["post_count"] == 12
