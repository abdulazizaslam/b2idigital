#!/usr/bin/env python3
"""
Rebuild the press-release feed and push it to HubSpot, keeping the same URL.

Replaces the existing HubSpot file in place via PUT /files/v3/files/{fileId},
so https://b2idigital.com/hubfs/press-release-feed.xml never changes address.

Run it on a schedule. New press releases published to the press-release blog
are picked up automatically on the next run.

Environment:
  HUBSPOT_TOKEN   private app token with the "files" write scope  (required)
  HUBSPOT_FILE_ID numeric id of the file to replace (default: the live one)

Usage:
  python publish_feed.py            # build, compare, upload only if changed
  python publish_feed.py --force    # upload regardless
  python publish_feed.py --dry-run  # build and report, never upload
"""

import argparse
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

from build_pressrelease_feed import build, SELF_URL

FILE_ID = os.environ.get("HUBSPOT_FILE_ID", "220866599267")
FILE_NAME = "press-release-feed.xml"
API = "https://api.hubapi.com/files/v3/files/%s"
# Matches the file's existing settings in HubSpot -- both must be sent on replace.
OPTIONS = '{"access":"PUBLIC_INDEXABLE","overwrite":true}'
FOLDER_PATH = "/"


def significant(xml):
    """Feed content ignoring lastBuildDate, which changes on every build."""
    return re.sub(r"<lastBuildDate>.*?</lastBuildDate>", "", xml)


def current_live():
    try:
        req = urllib.request.Request(SELF_URL, headers={"User-Agent": "b2i-feed-publisher/1.0",
                                                        "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print("could not read live feed (%s); will upload" % e)
        return None


def multipart(xml_bytes):
    """Build a multipart/form-data body without pulling in a dependency."""
    boundary = "----b2ifeed%s" % uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(("--%s\r\n"
                      "Content-Disposition: form-data; name=\"%s\"\r\n\r\n"
                      "%s\r\n" % (boundary, name, value)).encode())

    field("options", OPTIONS)
    field("folderPath", FOLDER_PATH)
    field("fileName", FILE_NAME)
    parts.append(("--%s\r\n"
                  "Content-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                  "Content-Type: application/xml\r\n\r\n" % (boundary, FILE_NAME)).encode())
    parts.append(xml_bytes)
    parts.append(("\r\n--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


def upload(xml, token):
    body, ctype = multipart(xml.encode("utf-8"))
    req = urllib.request.Request(API % FILE_ID, data=body, method="PUT")
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print("uploaded: HTTP %s" % r.status)
            return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        print("UPLOAD FAILED: HTTP %s\n%s" % (e.code, detail), file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="upload even if unchanged")
    ap.add_argument("--dry-run", action="store_true", help="build only, never upload")
    ap.add_argument("-o", "--output", default=FILE_NAME, help="also write a local copy")
    a = ap.parse_args()

    xml = build()
    items = xml.count("<item>")
    with open(a.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)
    print("built %s: %d bytes, %d item(s)" % (a.output, len(xml.encode()), items))

    print("no published press releases - publishing an empty feed")

    if a.dry_run:
        print("dry run - not uploading")
        return

    live = current_live()
    if live is not None and not a.force and significant(live) == significant(xml):
        print("no change since last publish - skipping upload")
        return

    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        sys.exit("HUBSPOT_TOKEN is not set")

    if not upload(xml, token):
        sys.exit(1)


if __name__ == "__main__":
    main()
