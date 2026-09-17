#!/usr/bin/env python3
"""
Build a clean, full-text RSS 2.0 feed for the B2i Digital Press Release blog.

Reads published posts directly from HubSpot's CMS Blog Posts API (live data,
not a cached feed file) and rewrites them into a distributor-ready feed:

  - full release text in BOTH <description> and <content:encoded> (CDATA)
  - HubSpot __ptq.gif tracking pixel removed
  - HubSpot featured-image wrapper div removed
  - duplicate in-body <h1> headline removed (it already lives in <title>)
  - inline style="" attributes and editor artifacts stripped
  - <media:content>, <media:thumbnail> and <enclosure> for the header image
  - <guid isPermaLink="true">, <atom:link rel="self">, <lastBuildDate>
  - <dc:creator>B2i Digital</dc:creator> instead of a personal-domain author

The output file's structure is unchanged from the previous rss.xml-based
builder -- same channel tags, same <item> fields, same cleanup pipeline.
Only the data source changed, from a HubSpot-cached rss.xml (which lagged
behind publish/unpublish changes) to the live Blog Posts API.

Requires HUBSPOT_TOKEN with the "content" (blog posts read) scope.

Usage:  python build_pressrelease_feed.py [-o OUTPUT.xml]
"""

import argparse
import html
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from email.utils import format_datetime, parsedate_to_datetime
from datetime import datetime, timezone

API_BASE = "https://api.hubapi.com/cms/v3/blogs/posts"
CONTENT_GROUP_ID = "220765531831"     # the "Press Release" blog
SELF_URL = "https://b2idigital.com/hubfs/press-release-feed.xml"

CHANNEL_TITLE = "B2i Digital Press Releases"
CHANNEL_LINK = "https://b2idigital.com/press-release"
CHANNEL_DESC = ("Full-text press releases distributed by B2i Digital, Inc. "
                "on behalf of its client companies.")
CREATOR = "B2i Digital"
CATEGORY = "Press Release"
UA = {"User-Agent": "b2i-feed-builder/1.0"}


def fetch(url, binary=False, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def fetch_published_posts(token, content_group_id=CONTENT_GROUP_ID):
    """Return published posts in a blog, newest first, straight from HubSpot's
    live CMS API -- no cached/pre-rendered feed file in between."""
    params = {
        "contentGroupId": content_group_id,
        "state": "PUBLISHED",
        "sort": "-publishDate",
        "limit": "100",
    }
    posts, after = [], None
    while True:
        q = dict(params)
        if after:
            q["after"] = after
        url = "%s?%s" % (API_BASE, urllib.parse.urlencode(q))
        raw = fetch(url, headers={"Authorization": "Bearer %s" % token})
        data = json.loads(raw)
        posts.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return posts


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


def cdata(s):
    return CDATA_OPEN + s.replace(CDATA_CLOSE, "]]&gt;") + CDATA_CLOSE


def iso_to_rfc822(iso):
    """HubSpot API dates are ISO 8601 ('2026-09-01T14:00:36Z'); item pubDates
    are RFC 822 ('Tue, 01 Sep 2026 14:00:36 GMT'), same as before."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def build(token=None, self_url=SELF_URL, content_group_id=CONTENT_GROUP_ID):
    token = token or os.environ.get("HUBSPOT_TOKEN")
    if not token:
        sys.exit("HUBSPOT_TOKEN is not set")

    posts = fetch_published_posts(token, content_group_id)
    if not posts:
        sys.exit("No published posts found in blog %s" % content_group_id)

    out = []
    for post in posts:
        title = post.get("htmlTitle") or post.get("name") or ""
        link = post.get("url") or ""
        guid = link
        pub = iso_to_rfc822(post.get("publishDate"))

        body_raw = post.get("postBody") or ""
        img = first_image(body_raw)
        body = clean_body(body_raw, title)

        parts = [
            "    <title>%s</title>" % esc(title),
            "    <link>%s</link>" % esc(link),
            '    <guid isPermaLink="true">%s</guid>' % esc(guid),
            "    <pubDate>%s</pubDate>" % pub,
            "    <dc:creator>%s</dc:creator>" % CREATOR,
            "    <category>%s</category>" % esc(CATEGORY),
        ]
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
        newest = parsedate_to_datetime(iso_to_rfc822(posts[0].get("publishDate")))
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
        # Republished hourly, so tell readers not to poll harder than that.
        "    <ttl>60</ttl>",
        "    <generator>B2i Digital press-release feed builder</generator>",
    ]
    return "\n".join(head) + "\n" + "\n".join(out) + "\n  </channel>\n</rss>\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", default="pressreleasefeed.xml")
    ap.add_argument("--self-url", default=SELF_URL)
    ap.add_argument("--content-group-id", default=CONTENT_GROUP_ID)
    a = ap.parse_args()
    xml = build(self_url=a.self_url, content_group_id=a.content_group_id)
    with open(a.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)
    print("wrote %s (%d bytes, %d items)" % (a.output, len(xml.encode()), xml.count("<item>")))
