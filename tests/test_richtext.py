from app.richtext import plaintext, richtext


def test_calibre_html_is_rendered_not_escaped():
    out = richtext('<div>\n<p>Stephen King egyik <b>legjobb</b> regénye.</p><p>Második bekezdés.</p></div>')
    assert "<div>" not in out and "&lt;" not in out
    assert out.count("<p>") == 2 and "<b>legjobb</b>" in out


def test_dangerous_content_removed():
    out = richtext('<p onclick="x()">Szia<script>alert(1)</script><img src=x onerror=y></p><a href="javascript:z">l</a><a href="https://moly.hu/x">m</a>')
    assert "script" not in out and "alert" not in out and "onclick" not in out and "<img" not in out
    assert "javascript" not in out and 'href="https://moly.hu/x"' in out


def test_plain_text_paragraphs():
    out = richtext("Első sor\nmásodik sor\n\nÚj bekezdés <nem címke")
    assert out == "<p>Első sor<br>második sor</p>\n<p>Új bekezdés &lt;nem címke</p>"


def test_unclosed_tags_closed():
    assert richtext("<p><b>félkész").endswith("</b></p>")


def test_plaintext():
    assert plaintext("<p>A</p><p>B &amp; C</p>") == "A\nB & C"
