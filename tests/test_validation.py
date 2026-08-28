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


async def _writer_headers(client):
    response = await client.post(
        "/api/v1/auth/login", data={"username": "writer@test.com", "password": "WriterPass123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_post_content_requires_blocks_list(client):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={"title": "Validation test", "content_json": {"nope": True}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "content_json"


async def test_post_content_rejects_unknown_block_type(client):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Validation test",
            "content_json": {"blocks": [{"type": "script", "text": "alert(1)"}]},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "content_json"


async def test_post_content_rejects_oversized_payload(client):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Validation test",
            "content_json": {"blocks": [{"type": "paragraph", "text": "x" * 600000}]},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "content_json"


async def test_post_content_accepts_all_supported_blocks(client, admin_token):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Block coverage test",
            "content_json": {
                "blocks": [
                    {"type": "paragraph", "text": "intro"},
                    {"type": "heading", "text": "section", "level": 2},
                    {"type": "quote", "text": "quoted"},
                    {"type": "code", "text": "SUM(A1:A9)", "language": "formula"},
                    {"type": "list", "items": ["one", "two"], "ordered": True},
                    {"type": "html", "html": "<div>embed</div>"},
                    {"type": "image", "url": "https://example.com/a.png", "alt": "chart"},
                    {"type": "table", "rows": [["a", "b"], ["c", "d"]], "header": True},
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    post_id = response.json()["id"]
    await client.delete(f"/api/v1/posts/{post_id}", headers={"Authorization": f"Bearer {admin_token}"})

async def test_post_content_accepts_inline_marks_align_and_hr(client, admin_token):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Inline marks test",
            "content_json": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "bold intro",
                        "content": [
                            {"text": "bold ", "marks": [{"type": "bold"}]},
                            {
                                "text": "link",
                                "marks": [{"type": "link", "href": "https://example.com"}],
                            },
                        ],
                        "align": "center",
                    },
                    {"type": "hr"},
                    {
                        "type": "list",
                        "items": [
                            [
                                {"text": "rich item", "marks": [{"type": "italic"}]},
                            ],
                            "plain item",
                        ],
                        "ordered": False,
                    },
                    {
                        "type": "table",
                        "rows": [[[{"text": "cell", "marks": [{"type": "code"}]}], "b"]],
                        "header": True,
                    },
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    post_id = response.json()["id"]
    await client.delete(f"/api/v1/posts/{post_id}", headers={"Authorization": f"Bearer {admin_token}"})


async def test_post_content_rejects_unsafe_link_href(client):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Unsafe link test",
            "content_json": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "xss",
                        "content": [
                            {"text": "click", "marks": [{"type": "link", "href": "javascript:alert(1)"}]},
                        ],
                    }
                ]
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "content_json"


async def test_post_content_rejects_unknown_mark_type(client):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Bad mark test",
            "content_json": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "styled",
                        "content": [{"text": "styled", "marks": [{"type": "underline"}]}],
                    }
                ]
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "content_json"


async def test_post_content_rejects_bad_align(client):
    headers = await _writer_headers(client)
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": "Bad align test",
            "content_json": {
                "blocks": [{"type": "paragraph", "text": "off", "align": "justify"}]
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "content_json"
