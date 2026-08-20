async def test_unknown_field_rejected_with_envelope(client):
    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "a@b.com", "is_admin": True}
    )
    assert response.status_code == 422
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["status"] == 422
    assert body["error"]["details"][0]["field"] == "is_admin"


async def test_bad_type_uses_envelope(client):
    response = await client.post("/api/v1/auth/forgot-password", json={"email": "not-an-email"})
    assert response.status_code == 422
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"][0]["field"] == "email"


async def test_unknown_field_rejected_on_post_create(client, admin_token):
    response = await client.post(
        "/api/v1/posts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "title": "Validation test",
            "content_json": {"blocks": []},
            "unexpected": "field",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "unexpected"


async def test_malformed_json_uses_envelope(client):
    response = await client.post(
        "/api/v1/auth/forgot-password",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
