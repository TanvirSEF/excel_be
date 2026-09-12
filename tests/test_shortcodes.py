from app.utils.shortcodes import expand_shortcodes


def test_su_highlight_becomes_kbd():
    assert expand_shortcodes("[su_highlight]Ctrl[/su_highlight]") == "<kbd>Ctrl</kbd>"


def test_su_highlight_with_attributes():
    src = '[su_highlight background="#DDFF99" color="#000000"]Shift[/su_highlight]'
    assert expand_shortcodes(src) == "<kbd>Shift</kbd>"


def test_titlebox_becomes_tip_callout():
    src = '[wpsm_titlebox title="Key Takeaways" style="main"]<strong>Steps</strong>[/wpsm_titlebox]'
    assert expand_shortcodes(src) == (
        '<div data-callout="" data-variant="tip" data-title="Key Takeaways">'
        "<strong>Steps</strong></div>"
    )


def test_titlebox_without_title_omits_attribute():
    src = '[wpsm_titlebox style="main"]content[/wpsm_titlebox]'
    assert expand_shortcodes(src) == '<div data-callout="" data-variant="tip">content</div>'


def test_box_maps_variants():
    solid = '[wpsm_box type="solid_border" float="none" textalign="center"]<code>=A1</code>[/wpsm_box]'
    assert expand_shortcodes(solid) == '<div data-callout="" data-variant="info"><code>=A1</code></div>'
    warning = '[wpsm_box type="warning" float="none" textalign="left"]Careful[/wpsm_box]'
    assert expand_shortcodes(warning) == '<div data-callout="" data-variant="warning">Careful</div>'


def test_numhead_becomes_data_numhead_heading():
    src = '[wpsm_numhead num="2" style="3" heading="2"]Using Excel&#8217;s Built-In Method[/wpsm_numhead]'
    assert expand_shortcodes(src) == '<h2 data-numhead="2">Using Excel&#8217;s Built-In Method</h2>'


def test_numhead_without_num_keeps_plain_heading():
    src = '[wpsm_numhead heading="3"]Plain Heading[/wpsm_numhead]'
    assert expand_shortcodes(src) == "<h3>Plain Heading</h3>"


def test_sc_self_closing_unescapes_content():
    src = '[sc:name="legend_box" title="Explanation" content="The <strong>VALUE</strong> function."]'
    assert expand_shortcodes(src) == (
        '<div data-callout="" data-variant="info" data-title="Explanation">'
        "The <strong>VALUE</strong> function.</div>"
    )


def test_sc_enclosed_form():
    src = '[sc name="legend_box" title="Explanation"]Your <em>content</em> here.[/sc]'
    assert expand_shortcodes(src) == (
        '<div data-callout="" data-variant="info" data-title="Explanation">'
        "Your <em>content</em> here.</div>"
    )


def test_excel_formula_brackets_untouched():
    src = "Insert &[Pages] then [type='submit'] and [local-name()='EUR'] plus [gallery]"
    assert expand_shortcodes(src) == src


def test_unknown_and_unclosed_shortcodes_untouched():
    src = '[wpsm_unknown foo="1"]x[/wpsm_unknown] and [wpsm_titlebox title="Broken"'
    assert expand_shortcodes(src) == src


def test_expansion_is_idempotent():
    src = '[wpsm_titlebox title="Key Takeaways" style="main"]A [su_highlight]B[/su_highlight][/wpsm_titlebox]'
    once = expand_shortcodes(src)
    assert expand_shortcodes(once) == once
