#!/usr/bin/env python3
"""
Manually remove one item from the already-published hosted feed, bypassing
the normal HubSpot-source rebuild.

Use this only when HubSpot's own press-release blog has been correctly
unpublished but HubSpot's native rss.xml (build_pressrelease_feed.py's
source) has not caught up yet, and you need the hosted feed corrected
immediately rather than waiting for HubSpot to regenerate its own feed.

Usage:
  HUBSPOT_TOKEN=... python remove_feed_item.py --match "aerospace-and-defense"
  HUBSPOT_TOKEN=... python remove_feed_item.py --match "..." --dry-run
"""

import argparse
import os
import re
import sys
from email.utils import format_datetime
from datetime import datetime, timezone

from publish_feed import current_live, upload
from build_pressrelease_feed import SELF_URL


def remove_items(xml, match):
    items = re.findall(r"  <item>.*?</item>\n", xml, re.S)
    kept, removed = [], []
    for item in items:
        (removed if match in item else kept).append(item)
    if not removed:
        sys.exit("no <item> matched %r -- nothing removed" % match)

    out = xml
    for item in removed:
        out = out.replace(item, "")
    out = re.sub(
        r"<lastBuildDate>.*?</lastBuildDate>",
        "<lastBuildDate>%s</lastBuildDate>" % format_datetime(datetime.now(timezone.utc), usegmt=True),
        out,
    )
    return out, len(removed), len(kept)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True,
                     help="substring (e.g. a slug) identifying the <item> block(s) to remove")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, never upload")
    a = ap.parse_args()

    live = current_live()
    if live is None:
        sys.exit("could not fetch the current hosted feed at %s" % SELF_URL)

    new_xml, n_removed, n_kept = remove_items(live, a.match)
    print("removed %d item(s), %d item(s) remain" % (n_removed, n_kept))

    if a.dry_run:
        print("dry run - not uploading")
        return

    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        sys.exit("HUBSPOT_TOKEN is not set")

    if not upload(new_xml, token):
        sys.exit(1)
    print("uploaded corrected feed (%d item(s) remaining)" % n_kept)


if __name__ == "__main__":
    main()
