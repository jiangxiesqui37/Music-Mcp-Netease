#!/usr/bin/env python3
"""Read-only proof for rebuilding full NetEase playlists from trackIds.

This script does not modify the account. It verifies whether playlist/detail exposes
all trackIds even when the returned `tracks` array is truncated, then resolves a
small sample of those IDs through song/detail.

Usage:
    python3 scripts/check_netease_full_tracks.py

Reads MUSIC_U from the environment or server/.netease_cred. Never prints it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
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


def request_json(path: str, music_u: str):
    req = Request(
        BASE + path,
        headers={
            "Cookie": f"MUSIC_U={music_u}",
            "Referer": BASE,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def resolve_sample(ids: list[int], music_u: str, sample_size: int = 25) -> int:
    sample = [int(x) for x in ids[:sample_size] if str(x).isdigit()]
    if not sample:
        return 0
    joined = ",".join(str(x) for x in sample)
    detail = request_json(f"/api/song/detail?ids=[{joined}]", music_u)
    return len(detail.get("songs") or [])


def main() -> int:
    music_u = load_music_u()
    if not music_u:
        print("ERROR: MUSIC_U not found")
        return 2

    account = request_json("/api/nuser/account/get", music_u)
    profile = account.get("profile") or {}
    acct = account.get("account") or {}
    uid = profile.get("userId") or acct.get("id")
    if not uid:
        print("ERROR: cookie did not resolve to an account")
        return 3

    data = request_json(f"/api/user/playlist?uid={uid}&limit=50&offset=0", music_u)
    playlists = data.get("playlist") or []

    print("NetEase full-track read-only proof")
    print(f"account: {profile.get('nickname') or ''} (uid={uid})")
    print(f"playlists: {len(playlists)}")
    print()

    interesting = []
    for p in playlists:
        advertised = int(p.get("trackCount") or 0)
        if advertised > 20:
            interesting.append(p)

    for p in interesting:
        pid = p.get("id")
        name = p.get("name") or ""
        advertised = int(p.get("trackCount") or 0)
        mine = (p.get("creator") or {}).get("userId") == uid
        try:
            detail = request_json(f"/api/v6/playlist/detail?id={pid}&n=1000&s=0", music_u)
            pl = detail.get("playlist") or {}
            tracks = pl.get("tracks") or []
            track_ids_raw = pl.get("trackIds") or []
            track_ids = [row.get("id") for row in track_ids_raw if isinstance(row, dict) and row.get("id")]
            resolved = resolve_sample(track_ids, music_u)
            print(
                f"- {name} | mine={mine} | advertised={advertised} | "
                f"tracks={len(tracks)} | trackIds={len(track_ids)} | "
                f"sampleResolved={resolved}/{min(25, len(track_ids))}"
            )
        except Exception as exc:
            print(f"- {name} | ERROR={type(exc).__name__}: {exc}")

    print()
    print("cookie: hidden")
    print("writes performed: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
