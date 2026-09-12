"""Expand WordPress plugin shortcodes into semantic HTML.

Covers the shortcodes left behind by the WordPress migration: ReHub theme
(wpsm_titlebox, wpsm_numhead, wpsm_box), Shortcodes Ultimate (su_highlight)
and Shortcoder ([sc] boxes). Unknown bracket text — e.g. Excel formulas like
[Pages] or XPath [local-name()='EUR'] — is left untouched.

Emits div[data-callout] markup that survives sanitize_html and converts to
callout blocks, so expanded content stays editable in the block editor.
"""
import html
import re

_ATTRS = r'(?:\s*[a-zA-Z_]+="[^"]*")*'
_ATTR = re.compile(r'([a-zA-Z_]+)="([^"]*)"')

_TITLEBOX_RE = re.compile(r"\[wpsm_titlebox(" + _ATTRS + r")\](.*?)\[/wpsm_titlebox\]", re.DOTALL)
_BOX_RE = re.compile(r"\[wpsm_box(" + _ATTRS + r")\](.*?)\[/wpsm_box\]", re.DOTALL)
_NUMHEAD_RE = re.compile(r"\[wpsm_numhead(" + _ATTRS + r")\](.*?)\[/wpsm_numhead\]", re.DOTALL)
_HIGHLIGHT_RE = re.compile(r"\[su_highlight" + _ATTRS + r"\](.*?)\[/su_highlight\]", re.DOTALL)
_SC_ENCLOSED_RE = re.compile(r"\[sc[:\s](" + _ATTRS + r")\](.*?)\[/sc\]", re.DOTALL)
_SC_SELF_RE = re.compile(r"\[sc[:\s]" + _ATTRS + r"\]", re.DOTALL)

_BOX_VARIANTS = {"warning": "warning", "danger": "danger"}


def _callout(inner: str, title: str = "", variant: str = "info") -> str:
    attrs = f' data-callout="" data-variant="{variant}"'
    if title:
        attrs += f' data-title="{title}"'
    return f"<div{attrs}>{inner}</div>"


def _numhead(attrs: dict[str, str], inner: str) -> str:
    try:
        level = min(max(int(attrs.get("heading", 2)), 1), 6)
    except ValueError:
        level = 2
    num = attrs.get("num", "").strip()
    if num:
        return f'<h{level} data-numhead="{html.escape(num)}">{inner}</h{level}>'
    return f"<h{level}>{inner}</h{level}>"


def expand_shortcodes(content: str) -> str:
    def titlebox(match: re.Match) -> str:
        attrs = dict(_ATTR.findall(match.group(1)))
        return _callout(match.group(2), title=attrs.get("title", ""), variant="tip")

    def box(match: re.Match) -> str:
        attrs = dict(_ATTR.findall(match.group(1)))
        variant = _BOX_VARIANTS.get(attrs.get("type", ""), "info")
        return _callout(match.group(2), variant=variant)

    def numhead(match: re.Match) -> str:
        attrs = dict(_ATTR.findall(match.group(1)))
        return _numhead(attrs, match.group(2).strip())

    def sc_enclosed(match: re.Match) -> str:
        attrs = dict(_ATTR.findall(match.group(1)))
        return _callout(match.group(2), title=attrs.get("title", ""))

    def sc_self(match: re.Match) -> str:
        attrs = dict(_ATTR.findall(match.group(0)))
        if "content" not in attrs:
            return match.group(0)
        return _callout(html.unescape(attrs["content"]), title=attrs.get("title", ""))

    expanded = _SC_ENCLOSED_RE.sub(sc_enclosed, content)
    expanded = _SC_SELF_RE.sub(sc_self, expanded)
    expanded = _TITLEBOX_RE.sub(titlebox, expanded)
    expanded = _BOX_RE.sub(box, expanded)
    expanded = _NUMHEAD_RE.sub(numhead, expanded)
    expanded = _HIGHLIGHT_RE.sub(lambda m: f"<kbd>{m.group(1)}</kbd>", expanded)
    return expanded
