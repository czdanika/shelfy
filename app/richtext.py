"""Könyvleírások biztonságos megjelenítése.

A fájlokból és az online forrásokból érkező leírás gyakran HTML (<div><p>…).
Csak egy szűk, ártalmatlan elemkészletet tartunk meg; minden más címke kimarad (a szövege marad),
a script/style tartalma teljesen. A sima szöveges leírás bekezdéseket és sortöréseket kap.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

ALLOWED = {"p", "br", "b", "strong", "i", "em", "u", "ul", "ol", "li", "blockquote", "h3", "h4", "a"}
VOID = {"br"}
DROP = {"script", "style", "iframe", "object", "embed", "head", "title", "svg", "math", "form"}
BLOCKS = {"div", "section", "article", "table", "tr", "h1", "h2", "h5", "h6"}  # bekezdéssé alakul


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in BLOCKS:
            self.out.append("\n\n")
            return
        if tag not in ALLOWED:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not re.match(r"https?://", href, re.I):
                return
            self.out.append(f'<a href="{html.escape(href, quote=True)}" rel="noopener nofollow" target="_blank">')
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self.skip:
            self.out.append("<br>")

    def handle_endtag(self, tag):
        if tag in DROP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in BLOCKS:
            self.out.append("\n\n")
            return
        if tag in self.stack:  # a nyitva maradt belső elemeket is lezárjuk
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def _paragraphs(text: str) -> str:
    """Laza szövegrészekből (dupla sortörés) bekezdések; a már <p>-be tett részek maradnak."""
    parts = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
    out = []
    for part in parts:
        if re.match(r"<(p|ul|ol|blockquote|h3|h4)\b", part):
            out.append(part)
        else:
            out.append("<p>" + part.replace("\n", "<br>") + "</p>")
    return "\n".join(out)


def richtext(value: str | None) -> str:
    if not value:
        return ""
    if not re.search(r"</?(p|div|br|b|i|u|em|strong|span|font|ul|ol|li|a|h\d|blockquote|table|section|script|style)\b[^>]*>", value, re.I):  # sima szöveg
        return _paragraphs(html.escape(value.strip(), quote=False))
    p = _Clean()
    p.feed(value)
    p.close()
    cleaned = re.sub(r"<p>\s*</p>", "", p.result())
    return _paragraphs(cleaned)


def plaintext(value: str | None) -> str:
    """HTML nélküli változat (pl. rövid előnézethez)."""
    if not value:
        return ""
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", value, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()
