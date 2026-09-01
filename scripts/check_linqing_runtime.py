#!/usr/bin/env python3
"""Privacy-safe runtime smoke check for ``server/music_linqing.py``.

Requires the overlay server to be listening on localhost. It prints counts only:
no account name, uid, playlist name, song title, artist, or cookie.
"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

BASE = os.environ.get("MUSIC_TEST_BASE", "http://127.0.0.1:19090").rstrip("/")
GATEWAY = os.environ.get("MUSIC_GATEWAY_TOKEN", "music-gateway")
PAGE_SIZE = 500


def get_json(path: str):
    req = Request(
        BASE + path,
        headers={"X-Music-Gateway": GATEWAY, "User-Agent": "linqing-runtime-check/1"},
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    health = get_json("/health")
    if not health.get("ok"):
        print("ERROR: health check failed")
        return 2

    listing = get_json("/music/netease/playlists")
    playlists = [p for p in (listing.get("playlists") or []) if p.get("mine")]
    if not playlists:
        print("ERROR: no owned playlists returned")
        return 3

    # The liked playlist is normally the largest owned playlist. We deliberately do not
    # print its name/id to keep diagnostics out of chat logs.
    target = max(playlists, key=lambda p: int(p.get("count") or 0))
    pid = target.get("id")
    expected = int(target.get("count") or 0)
    if not pid:
        print("ERROR: target playlist has no id")
        return 4

    offset = 0
    total_returned = 0
    page = 0
    seen_total = None
    while True:
        data = get_json(f"/music/netease/playlist?id={pid}&offset={offset}&limit={PAGE_SIZE}")
        if not data.get("ok"):
            print(f"ERROR: page {page + 1} failed")
            return 5
        page += 1
        returned = int(data.get("returned") or len(data.get("songs") or []))
        available = int(data.get("availableTotal") or 0)
        total = int(data.get("total") or 0)
        more = bool(data.get("more"))
        next_offset = data.get("nextOffset")
        seen_total = total
        total_returned += returned
        print(
            f"page={page} offset={offset} returned={returned} "
            f"availableTotal={available} total={total} more={str(more).lower()}"
        )
        if not more:
            break
        if next_offset is None or int(next_offset) <= offset:
            print("ERROR: pagination did not advance")
            return 6
        offset = int(next_offset)

    print(f"expected={expected} returned_all={total_returned} reported_total={seen_total}")
    print("private fields printed: 0")
    print("writes performed: 0")
    return 0 if expected == total_returned == seen_total else 7


if __name__ == "__main__":
    raise SystemExit(main())
