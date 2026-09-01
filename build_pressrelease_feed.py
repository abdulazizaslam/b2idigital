#!/usr/bin/env python3
"""
Build a clean, full-text RSS 2.0 feed for the B2i Digital Press Release blog.

Reads HubSpot's native feed at https://b2idigital.com/press-release/rss.xml and
rewrites it into a distributor-ready feed:

  - full release text in BOTH <description> and <content:encoded> (CDATA)
  - HubSpot __ptq.gif tracking pixel removed
  - HubSpot featured-image wrapper div removed
  - duplicate in-body <h1> headline removed (it already lives in <title>)
  - inline style="" attributes and editor artifacts stripped
  - <media:content>, <media:thumbnail> and <enclosure> for the header image
  - <guid isPermaLink="true">, <atom:link rel="self">, <lastBuildDate>
  - <dc:creator>B2i Digital</dc:creator> instead of a personal-domain author

Usage:  python build_pressrelease_feed.py [-o OUTPUT.xml]
"""

import argparse
import html
import io
import re
import sys
import urllib.request
from email.utils import format_datetime, parsedate_to_datetime
from datetime import datetime, timezone

SOURCE_FEED = "https://b2idigital.com/press-release/rss.xml"
SELF_URL = "https://b2idigital.com/hubfs/press-release-feed.xml"

CHANNEL_TITLE = "B2i Digital Press Releases"
CHANNEL_LINK = "https://b2idigital.com/press-release"
CHANNEL_DESC = ("Full-text press releases distributed by B2i Digital, Inc. "
                "on behalf of its client companies.")
CREATOR = "B2i Digital"
UA = {"User-Agent": "b2i-feed-builder/1.0"}


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def strip_tag_block(markup, tag, class_name):
    """Remove <tag class="...class_name..."> ... </tag>, handling nesting."""
    pattern = re.compile(
        r'<%s\b[^>]*class="[^"]*%s[^"]*"[^>]*>' % (tag, re.escape(class_name)), re.I)
    open_re = re.compile(r"<%s\b" % tag, re.I)
    close_re = re.compile(r"</%s\s*>" % tag, re.I)
    while True:
        m = pattern.search(markup)
        if not m:
            return markup
        start = m.start()
        depth, pos, end = 0, m.start(), None
        while pos < len(markup):
            o = open_re.search(markup, pos)
            c = close_re.search(markup, pos)
            if not c:
                break
            if o and o.start() < c.start():
                depth += 1
                pos = o.end()
            else:
                depth -= 1
                pos = c.end()
                if depth == 0:
                    end = pos
                    break
        if end is None:
            return markup[:start]
        markup = markup[:start] + markup[end:]


def norm_text(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip().lower()


def drop_duplicate_h1(markup, title):
    """Remove an <h1> whose text repeats the item title."""
    for m in re.finditer(r"<h1\b[^>]*>(.*?)</h1>", markup, re.S | re.I):
        if norm_text(m.group(1)) == norm_text(title):
            return markup[:m.start()] + markup[m.end():]
    return markup


def clean_body(raw, title):
    b = raw
    b = strip_tag_block(b, "div", "hs-featured-image-wrapper")
    b = strip_tag_block(b, "div", "hs-embed-wrapper")             # video embeds
    b = re.sub(r"<img[^>]*__ptq\.gif[^>]*>", "", b, flags=re.I)   # tracking pixel
    b = re.sub(r"<iframe\b.*?</iframe\s*>", "", b, flags=re.S | re.I)
    b = drop_duplicate_h1(b, title)
    b = re.sub(r"<!--\s*\[if [^\]]*\]-->|<!--\[endif\]-->", "", b, flags=re.I)
    b = re.sub(r"\{\{.*?\}\}", "", b, flags=re.S)                 # stray HubL
    b = re.sub(r'\sdata-[\w-]+="[^"]*"', "", b)                   # data-* attrs
    b = re.sub(r'\sstyle="[^"]*"', "", b)                         # inline styles
    b = re.sub(r'\sloading="[^"]*"', "", b)
    # Whitespace-only spans are word separators in HubSpot output -- collapse them
    # to a real space rather than deleting them, or adjacent words run together.
    b = re.sub(r"<span>(?:\s|&nbsp;)*</span>", " ", b, flags=re.I)
    b = re.sub(r"</?span\s*>", "", b, flags=re.I)                 # unwrap bare spans
    b = re.sub(r"<p>(?:\s|&nbsp;|<br\s*/?>)*</p>", "", b, flags=re.I)
    b = re.sub(r"[ \t]{2,}", " ", b)
    b = re.sub(r"\n{3,}", "\n\n", b)
    # A bare "&" is legal inside CDATA but invalid HTML once a reader extracts
    # the markup. Escape any ampersand that is not already an entity.
    b = re.sub(r"&(?!#?\w+;)", "&amp;", b)
    return b.strip()


def first_image(raw_body):
    for m in re.finditer(r'<img[^>]+src="([^"]+)"[^>]*>', raw_body, re.I):
        src = html.unescape(m.group(1))
        if "__ptq.gif" in src:
            continue
        return src
    return None


def image_meta(url):
    """Return (mime, width, height, byte_length) for an image URL.

    byte_length is the real Content-Length -- RSS 2.0 requires <enclosure length>
    to be the actual size in bytes; some aggregators reject length="0".
    """
    try:
        from PIL import Image
        data = fetch(url, binary=True)
        im = Image.open(io.BytesIO(data))
        return Image.MIME.get(im.format, "image/jpeg"), im.width, im.height, len(data)
    except Exception:
        ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
        mime = {"png": "image/png", "gif": "image/gif",
                "webp": "image/webp"}.get(ext, "image/jpeg")
        return mime, None, None, None


def esc(s):
    return html.escape(s, quote=False)


CDATA_OPEN = "<![CDATA["
CDATA_CLOSE = "]]>"


def unwrap_field(raw):
    """Return the raw HTML of an RSS text field, however it was encoded.

    HubSpot emits the body wrapped in CDATA -- and currently emits it wrapped
    TWICE ("<![CDATA[<![CDATA[<p>..."). Re-wrapping that without unwrapping
    first leaks a literal "<![CDATA[" into the rendered text, which the W3C
    feed validator flags as invalid HTML. Strip every wrapper, then let cdata()
    add exactly one back.

    Content inside CDATA is already raw, so it must NOT be HTML-unescaped --
    doing so would turn a literal "&amp;" in the markup into a bare "&".
    """
    s = raw.strip()
    if CDATA_OPEN not in s:
        return html.unescape(s)
    # HubSpot wraps an already-wrapped body, escaping the inner terminator as
    # "]]&gt;". Peel one layer at a time, restoring that terminator each pass so
    # the next layer can be recognised.
    while s.startswith(CDATA_OPEN):
        if s.endswith(CDATA_CLOSE):
            s = s[len(CDATA_OPEN):-len(CDATA_CLOSE)].strip()
        else:
            s = s[len(CDATA_OPEN):].strip()
        s = s.replace("]]&gt;", CDATA_CLOSE).strip()
    # Any markers still embedded are HubSpot artifacts, never real content.
    return s.replace(CDATA_OPEN, "").replace(CDATA_CLOSE, "").strip()


def cdata(s):
    return CDATA_OPEN + s.replace(CDATA_CLOSE, "]]&gt;") + CDATA_CLOSE


def build(source=SOURCE_FEED, self_url=SELF_URL):
    src = fetch(source)
    items_raw = re.findall(r"<item>(.*?)</item>", src, re.S)
    if not items_raw:
        sys.exit("No <item> elements found in %s" % source)

    out = []
    for raw in items_raw:
        def tag(name, default=""):
            m = re.search(r"<%s>(.*?)</%s>" % (name, name), raw, re.S)
            return m.group(1).strip() if m else default

        title = unwrap_field(tag("title"))
        link = unwrap_field(tag("link"))
        guid = unwrap_field(tag("guid")) or link
        pub = tag("pubDate")
        category = unwrap_field(tag("category"))

        m = re.search(r"<content:encoded>(.*?)</content:encoded>", raw, re.S)
        body_raw = unwrap_field(m.group(1)) if m else ""
        img = first_image(body_raw)
        body = clean_body(body_raw, title)

        parts = [
            "    <title>%s</title>" % esc(title),
            "    <link>%s</link>" % esc(link),
            '    <guid isPermaLink="true">%s</guid>' % esc(guid),
            "    <pubDate>%s</pubDate>" % pub,
            "    <dc:creator>%s</dc:creator>" % CREATOR,
        ]
        if category:
            parts.append("    <category>%s</category>" % esc(category))
        if img:
            mime, w, h, nbytes = image_meta(img)
            dims = ' width="%d" height="%d"' % (w, h) if w and h else ""
            size = ' fileSize="%d"' % nbytes if nbytes else ""
            parts.append('    <media:content url="%s" medium="image" type="%s"%s%s />'
                         % (esc(img), mime, dims, size))
            parts.append('    <media:thumbnail url="%s"%s />' % (esc(img), dims))
            if nbytes:
                parts.append('    <enclosure url="%s" type="%s" length="%d" />'
                             % (esc(img), mime, nbytes))
        parts.append("    <description>%s</description>" % cdata(body))
        parts.append("    <content:encoded>%s</content:encoded>" % cdata(body))
        out.append("  <item>\n" + "\n".join(parts) + "\n  </item>")

    try:
        newest = parsedate_to_datetime(
            re.search(r"<pubDate>(.*?)</pubDate>", items_raw[0]).group(1))
    except Exception:
        newest = datetime.now(timezone.utc)

    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"',
        '     xmlns:content="http://purl.org/rss/1.0/modules/content/"',
        '     xmlns:dc="http://purl.org/dc/elements/1.1/"',
        '     xmlns:atom="http://www.w3.org/2005/Atom"',
        '     xmlns:media="http://search.yahoo.com/mrss/">',
        "  <channel>",
        "    <title>%s</title>" % esc(CHANNEL_TITLE),
        "    <link>%s</link>" % esc(CHANNEL_LINK),
        '    <atom:link href="%s" rel="self" type="application/rss+xml" />' % esc(self_url),
        "    <description>%s</description>" % esc(CHANNEL_DESC),
        "    <language>en-us</language>",
        # usegmt keeps the zone spelled "GMT", matching item pubDates. Both forms
        # are legal RFC 822, but mixing them looks like an inconsistency on review.
        "    <lastBuildDate>%s</lastBuildDate>" % format_datetime(newest, usegmt=True),
        "    <docs>https://www.rssboard.org/rss-specification</docs>",
        "    <generator>B2i Digital press-release feed builder</generator>",
    ]
    return "\n".join(head) + "\n" + "\n".join(out) + "\n  </channel>\n</rss>\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", default="pressreleasefeed.xml")
    ap.add_argument("--source", default=SOURCE_FEED)
    ap.add_argument("--self-url", default=SELF_URL)
    a = ap.parse_args()
    xml = build(a.source, a.self_url)
    with open(a.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)
    print("wrote %s (%d bytes, %d items)" % (a.output, len(xml.encode()), xml.count("<item>")))
