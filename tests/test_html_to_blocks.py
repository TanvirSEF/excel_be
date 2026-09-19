from app.utils.html_to_blocks import convert, is_broken


def blocks_of_type(doc, block_type):
    return [b for b in doc["blocks"] if b.get("type") == block_type]


def test_bold_paragraph_produces_runs():
    doc = convert("<p>Hello <strong>world</strong> and <b>again</b></p>")
    runs = doc["blocks"][0]["content"]
    assert {"text": "world", "marks": [{"type": "bold"}]} in runs
    assert {"text": "again", "marks": [{"type": "bold"}]} in runs


def test_kbd_tag_becomes_kbd_run():
    doc = convert("<p>Press <kbd>Ctrl</kbd> now</p>")
    runs = doc["blocks"][0]["content"]
    assert {"text": "Ctrl", "marks": [{"type": "kbd"}]} in runs


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


def test_titlebox_produces_tip_callout():
    html = '<div data-callout="" data-variant="tip" data-title="Key Takeaways"><p>Steps</p></div>'
    callout = blocks_of_type(convert(html), "callout")[0]
    assert callout["variant"] == "tip"
    assert callout["title"] == "Key Takeaways"


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


def test_numbered_heading_with_data_numhead():
    heading = blocks_of_type(
        convert('<h2 data-numhead="1">Using Built-In Method</h2>'), "heading"
    )[0]
    assert heading["level"] == 2
    assert heading["text"] == "Using Built-In Method"
    assert heading["num"] == "1"


def test_plain_heading_has_no_num():
    heading = blocks_of_type(convert("<h2>Plain Heading</h2>"), "heading")[0]
    assert heading["text"] == "Plain Heading"
    assert "num" not in heading


def test_is_broken_detects_empty_and_missing_docs():
    assert is_broken(None)
    assert is_broken({"blocks": []})
    assert is_broken({"blocks": [{"type": "paragraph", "text": ""}]})
    assert not is_broken({"blocks": [{"type": "paragraph", "text": "ok", "content": [{"text": "ok"}]}]})


def test_span_wrapped_image_extracted():
    html = '<p>Intro</p><span><img src="https://r2.dev/test.webp" alt="test" width="600" height="400"></span><p>Outro</p>'
    doc = convert(html)
    imgs = blocks_of_type(doc, "image")
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://r2.dev/test.webp"
    assert imgs[0]["alt"] == "test"
    assert imgs[0]["width"] == 600
    assert imgs[0]["height"] == 400


def test_anchor_wrapped_image_extracted():
    html = '<a href="https://example.com/view"><img src="https://r2.dev/thumb.webp" alt="thumb"></a>'
    doc = convert(html)
    imgs = blocks_of_type(doc, "image")
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://r2.dev/thumb.webp"
    assert imgs[0]["alt"] == "thumb"


def test_nested_spans_image_extracted():
    html = '<span><span><img src="https://r2.dev/nested.webp" alt="nested"></span></span>'
    doc = convert(html)
    imgs = blocks_of_type(doc, "image")
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://r2.dev/nested.webp"


def test_div_with_nested_span_images_extracted():
    html = '<div><span class="aligncenter"><img src="https://r2.dev/in-div.webp" alt="in div"></span></div>'
    doc = convert(html)
    imgs = blocks_of_type(doc, "image")
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://r2.dev/in-div.webp"


def test_document_level_strong_and_b_produce_runs():
    html = "Hello <strong>world</strong> and <b>again</b> without p tags"
    doc = convert(html)
    runs = doc["blocks"][0]["content"]
    assert {"text": "world", "marks": [{"type": "bold"}]} in runs
    assert {"text": "again", "marks": [{"type": "bold"}]} in runs


def test_document_level_link_and_code_produce_runs():
    html = 'Check out <a href="https://example.com">this link</a> and <code>=VLOOKUP()</code>'
    doc = convert(html)
    runs = doc["blocks"][0]["content"]
    assert {"text": "this link", "marks": [{"type": "link", "href": "https://example.com"}]} in runs
    assert {"text": "=VLOOKUP()", "marks": [{"type": "code"}]} in runs


def test_nested_marks_produce_multiple_marks():
    html = "<p><strong><em>bold italic</em></strong></p>"
    doc = convert(html)
    runs = doc["blocks"][0]["content"]
    assert len(runs) == 1
    mark_types = {m["type"] for m in runs[0]["marks"]}
    assert mark_types == {"bold", "italic"}


def test_underline_sup_and_sub_marks():
    html = "<p><u>underlined</u>, <sup>squared</sup>, and <sub>indexed</sub></p>"
    doc = convert(html)
    runs = doc["blocks"][0]["content"]
    assert {"text": "underlined", "marks": [{"type": "underline"}]} in runs
    assert {"text": "squared", "marks": [{"type": "sup"}]} in runs
    assert {"text": "indexed", "marks": [{"type": "sub"}]} in runs


def test_wpsm_button_with_nbsp_attributes():
    from app.utils.shortcodes import expand_shortcodes
    raw = '[wpsm_button link="https://example.com/file.xlsx" border_radius="4px"&nbsp; rel="nofollow"]Download File[/wpsm_button]'
    expanded = expand_shortcodes(raw)
    doc = convert(expanded)
    buttons = blocks_of_type(doc, "button")
    assert len(buttons) == 1
    assert buttons[0]["label"] == "Download File"
    assert buttons[0]["href"] == "https://example.com/file.xlsx"


def test_sc_legend_box_with_inner_quotes():
    from app.utils.shortcodes import expand_shortcodes
    raw = '[sc name="legend_box" title="Explanation" content="This has "quoted text" inside"]'
    expanded = expand_shortcodes(raw)
    doc = convert(expanded)
    callouts = blocks_of_type(doc, "callout")
    assert len(callouts) == 1
    assert callouts[0]["title"] == "Explanation"
    assert "quoted text" in callouts[0]["text"]


def test_unclosed_wpsm_titlebox():
    from app.utils.shortcodes import expand_shortcodes
    raw = '[wpsm_titlebox title="Key Takeaways" style="main"]\nTakeaway bullet 1\nTakeaway bullet 2\n<h2>Section 1</h2>'
    expanded = expand_shortcodes(raw)
    doc = convert(expanded)
    callouts = blocks_of_type(doc, "callout")
    assert len(callouts) == 1
    assert callouts[0]["title"] == "Key Takeaways"
    assert "Takeaway bullet 1" in callouts[0]["text"]


