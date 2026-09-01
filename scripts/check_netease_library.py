#!/usr/bin/env python3
"""Read-only audit for the NetEase account used by this music server.

What it checks:
- account uid / nickname
- all liked-song IDs returned by /api/song/like/get
- account playlists and their advertised track counts
- how many tracks the current playlist-detail endpoint returns at n=500 / n=1000

It never likes/unlikes, scrobbles, edits playlists, or writes account data.
It never prints the MUSIC_U cookie.

Usage:
    MUSIC_U='...' python3 scripts/check_netease_library.py

Or place MUSIC_U=... in server/.netease_cred and run:
    python3 scripts/check_netease_library.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CRED_FILE = ROOT / "server" / ".netease_cred"
BASE = "https://music.163.com"


def load_music_u() -> str:
    value = os.environ.get("MUSIC_U", "").strip()
    if value:
        return value
    try:
        for line in CRED_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("MUSIC_U="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def request_json(path: str, music_u: str, data: dict[str, str] | None = None):
    body = urlencode(data).encode() if data is not None else None
    req = Request(
        BASE + path,
        data=body,
        headers={
            "Cookie": f"MUSIC_U={music_u}",
            "Referer": BASE,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            **({"Content-Type": "application/x-www-form-urlencoded"} if body else {}),
        },
    )
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main() -> int:
    music_u = load_music_u()
    if not music_u:
        print("ERROR: MUSIC_U not found. Set env MUSIC_U or server/.netease_cred")
        return 2

    account = request_json("/api/nuser/account/get", music_u)
    profile = account.get("profile") or {}
    acct = account.get("account") or {}
    uid = profile.get("userId") or acct.get("id")
    nickname = profile.get("nickname") or ""
    if not uid:
        print("ERROR: cookie did not resolve to an account")
        return 3

    likes = request_json("/api/song/like/get", music_u)
    liked_ids = likes.get("ids") or []

    playlists_data = request_json(f"/api/user/playlist?uid={uid}&limit=50&offset=0", music_u)
    playlists = playlists_data.get("playlist") or []

    print("NetEase read-only library audit")
    print(f"account: {nickname} (uid={uid})")
    print(f"liked ids: {len(liked_ids)}")
    print(f"playlists returned: {len(playlists)}")
    print()

    for p in playlists:
        pid = p.get("id")
        name = p.get("name") or ""
        advertised = int(p.get("trackCount") or 0)
        owner = (p.get("creator") or {}).get("userId") == uid
        print(f"- {name} | id={pid} | advertised={advertised} | mine={owner}")

        if not pid or advertised <= 0:
            continue

        counts = []
        for n in (500, 1000):
            try:
                detail = request_json(f"/api/v6/playlist/detail?id={pid}&n={n}&s=0", music_u)
                tracks = (detail.get("playlist") or {}).get("tracks") or []
                counts.append(f"n={n}:returned={len(tracks)}")
            except Exception as exc:
                counts.append(f"n={n}:ERROR={type(exc).__name__}")
        print("  " + " | ".join(counts))

    print()
    print("cookie: hidden")
    print("writes performed: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
