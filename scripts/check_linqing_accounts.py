#!/usr/bin/env python3
"""Privacy-safe smoke check for Linqing NetEase account slots.

Requires ``server/music_linqing.py`` on localhost. Prints slot ids, configured flags,
and the default slot only. It never prints cookies, account profile data, playlist
names, song names, artists, or uid values.
"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

BASE = os.environ.get("MUSIC_TEST_BASE", "http://127.0.0.1:19090").rstrip("/")
GATEWAY = os.environ.get("MUSIC_GATEWAY_TOKEN", "music-gateway")


def get_json(path: str):
    req = Request(
        BASE + path,
        headers={
            "X-Music-Gateway": GATEWAY,
            "User-Agent": "linqing-account-check/1",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    data = get_json("/music/linqing/accounts")
    if not data.get("ok"):
        print("ERROR: account slot endpoint failed")
        return 2

    accounts = data.get("accounts") or []
    print(f"default={data.get('default') or ''}")
    for row in accounts:
        print(
            f"slot={row.get('id') or ''} "
            f"configured={str(bool(row.get('configured'))).lower()} "
            f"default={str(bool(row.get('default'))).lower()}"
        )

    # Prove the default slot still reaches the existing account without exposing it.
    default_id = str(data.get("default") or "")
    likes = get_json("/music/netease/likes?account=" + default_id)
    if not likes.get("ok"):
        print("ERROR: default account read failed")
        return 3
    print(f"default_liked_count={int(likes.get('count') or 0)}")
    print("private fields printed: 0")
    print("writes performed: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
