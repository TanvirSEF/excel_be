import nh3

TAGS = {
    "a", "abbr", "acronym", "area", "article", "aside", "b", "bdi", "bdo",
    "blockquote", "br", "caption", "center", "cite", "code", "col", "colgroup",
    "data", "dd", "del", "details", "dfn", "div", "dl", "dt", "em",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "i", "iframe", "img", "ins", "kbd", "li",
    "map", "mark", "ol", "p", "pre", "q", "rp", "rt", "ruby", "s", "samp",
    "small", "span", "strike", "strong", "sub", "summary", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "time", "tr", "tt", "u", "ul",
    "var", "wbr",
}

ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height", "loading"},
    "iframe": {"src", "width", "height", "title", "allow", "allowfullscreen", "frameborder"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "pre": {"class"},
    "code": {"class"},
    "span": {"class"},
    "div": {"class"},
    "figure": {"class"},
    "table": {"class"},
}

URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(html: str) -> str:
    return nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES)
