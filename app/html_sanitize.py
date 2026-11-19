"""Allowlist sanitizer for merchant detail HTML; dependency-free (html.parser).

Detail HTML is stored in MySQL and rendered in the storefront, so it must never
carry scripts, event handlers, or foreign URLs. Only presentational tags survive;
images are restricted to immutable /media/ object keys.
"""
import re
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = {'p', 'br', 'ul', 'ol', 'li', 'strong', 'b', 'em', 'i', 'h3', 'h4', 'h5',
                'table', 'thead', 'tbody', 'tr', 'td', 'th', 'img', 'span', 'div', 'blockquote'}
VOID_TAGS = {'br', 'img'}
DROP_WITH_CONTENT = {'script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'template'}
MEDIA_SRC = re.compile(r'/media/[a-f0-9]{64}\.(?:png|jpg|webp)')


def _safe_attributes(tag, attrs):
    kept = []
    for name, value in attrs:
        name = (name or '').lower()
        if value is None:
            continue
        if tag == 'img' and name == 'src' and MEDIA_SRC.fullmatch(value.strip()):
            kept.append(('src', value.strip()))
        elif tag == 'img' and name == 'alt':
            kept.append(('alt', value[:100]))
    return kept


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.suppress += 1
            return
        if self.suppress or tag not in ALLOWED_TAGS:
            return
        attributes = ''.join(f' {name}="{escape(value, quote=True)}"' for name, value in _safe_attributes(tag, attrs))
        self.parts.append(f'<{tag}{attributes}>' if tag not in VOID_TAGS else f'<{tag}{attributes}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and tag not in DROP_WITH_CONTENT and not self.suppress:
            self.parts.append(f'</{tag}>')

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.suppress = max(0, self.suppress - 1)
            return
        if self.suppress or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.suppress:
            self.parts.append(escape(data, quote=False))


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.suppress += 1
        elif tag == 'br':
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.suppress = max(0, self.suppress - 1)
        elif tag in {'p', 'div', 'li', 'tr', 'h3', 'h4', 'h5', 'blockquote'}:
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.suppress:
            self.parts.append(data)


def sanitize_html(value):
    parser = _Sanitizer()
    parser.feed(value or '')
    parser.close()
    return ''.join(parser.parts)


def text_content(value):
    parser = _TextExtractor()
    parser.feed(value or '')
    parser.close()
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()