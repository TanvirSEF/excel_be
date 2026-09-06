from app.utils.html_to_blocks import convert, is_broken


def blocks_of_type(doc, block_type):
    return [b for b in doc["blocks"] if b.get("type") == block_type]


def test_bold_paragraph_produces_runs():
    doc = convert("<p>Hello <strong>world</strong> and <b>again</b></p>")
    runs = doc["blocks"][0]["content"]
    assert {"text": "world", "marks": [{"type": "bold"}]} in runs
    assert {"text": "again", "marks": [{"type": "bold"}]} in runs


def test_mark_tag_becomes_highlight_run():
    doc = convert("<p>Press <mark>Ctrl</mark> now</p>")
    runs = doc["blocks"][0]["content"]
    assert {"text": "Ctrl", "marks": [{"type": "highlight"}]} in runs


def test_callout_block_shape():
    html = (
        '<div data-callout="" data-variant="info" data-title="Key Takeaways">'
        "<strong>Steps:</strong> select a cell<br><code>=A1</code></div>"
    )
    callouts = blocks_of_type(convert(html), "callout")
    assert len(callouts) == 1
    callout = callouts[0]
    assert callout["variant"] == "info"
    assert callout["title"] == "Key Takeaways"
    assert "Steps:" in callout["text"]
    assert {"text": "Steps:", "marks": [{"type": "bold"}]} in callout["content"]
    assert {"text": "=A1", "marks": [{"type": "code"}]} in callout["content"]
    assert {"text": "\n"} in callout["content"]


def test_callout_paragraphs_join_with_newlines():
    html = '<div data-callout="" data-variant="tip"><p>First</p><p>Second</p></div>'
    callout = blocks_of_type(convert(html), "callout")[0]
    assert {"text": "\n"} in callout["content"]
    assert callout["text"] == "First\nSecond"


def test_callout_hoists_images_after_block():
    html = (
        '<div data-callout="" data-variant="tip"><p>Text</p>'
        '<img src="https://r2.dev/a.webp" alt="pic"></div>'
    )
    blocks = convert(html)["blocks"]
    assert blocks[0]["type"] == "callout"
    assert blocks[1] == {"type": "image", "url": "https://r2.dev/a.webp", "alt": "pic"}


def test_callout_invalid_variant_falls_back_to_info():
    html = '<div data-callout="" data-variant="weird">text</div>'
    assert blocks_of_type(convert(html), "callout")[0]["variant"] == "info"


def test_numbered_heading():
    heading = blocks_of_type(convert("<h2>1. Using Built-In Method</h2>"), "heading")[0]
    assert heading["level"] == 2
    assert heading["text"] == "1. Using Built-In Method"


def test_is_broken_detects_empty_and_missing_docs():
    assert is_broken(None)
    assert is_broken({"blocks": []})
    assert is_broken({"blocks": [{"type": "paragraph", "text": ""}]})
    assert not is_broken({"blocks": [{"type": "paragraph", "text": "ok", "content": [{"text": "ok"}]}]})
