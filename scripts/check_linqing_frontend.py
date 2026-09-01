#!/usr/bin/env python3
"""Privacy-safe smoke check for the Linqing lazy playlist + account UI plumbing.

Requires ``server/music_linqing.py`` to be running on localhost. Prints counts and
feature flags only. No account nickname, uid, playlist name, song title, artist, or cookie.
"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

BASE = os.environ.get("MUSIC_TEST_BASE", "http://127.0.0.1:19090").rstrip("/")
GATEWAY = os.environ.get("MUSIC_GATEWAY_TOKEN", "music-gateway")


def get_bytes(path: str, auth: bool = False) -> bytes:
    headers = {"User-Agent": "linqing-frontend-check/1"}
    if auth:
        headers["X-Music-Gateway"] = GATEWAY
    req = Request(BASE + path, headers=headers)
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def get_json(path: str):
    return json.loads(get_bytes(path, auth=True))


def main() -> int:
    html = get_bytes("/").decode("utf-8", errors="replace")
    injected = '<script src="/linqing-pagination.js"></script>' in html

    js = get_bytes("/linqing-pagination.js").decode("utf-8", errors="replace")
    js_has_page_100 = "const PAGE_SIZE = 100" in js
    js_has_observer = "IntersectionObserver" in js
    js_has_lazy_renderer = "linqingRenderPlaylistDetail" in js
    js_has_account_key = "linqing-music-account" in js
    js_has_account_router = "u.searchParams.set('account', account)" in js
    js_has_account_labels = "qingqing: '卿卿'" in js and "linlin: '老公'" in js
    js_has_account_bar = "linqing-account-bar" in js
    js_has_hot_switch = (
        "linqingHotAccountSwitch" in js
        and "prefetchAccountCore" in js
        and "location.reload()" not in js
    )
    js_has_warm_cache = (
        "ACCOUNT_CACHE_TTL" in js
        and "accountReadCache" in js
        and "cachedAccountRead" in js
    )

    accounts = get_json("/music/linqing/accounts")
    slot_ids = [str(x.get("id") or "") for x in (accounts.get("accounts") or [])]
    has_two_slots = "qingqing" in slot_ids and "linlin" in slot_ids

    listing = get_json("/music/netease/playlists?account=qingqing")
    owned = [p for p in (listing.get("playlists") or []) if p.get("mine")]
    if not owned:
        print("ERROR: no owned playlists returned")
        return 2

    target = max(owned, key=lambda p: int(p.get("count") or 0))
    pid = target.get("id")
    expected = int(target.get("count") or 0)
    if not pid:
        print("ERROR: target playlist has no id")
        return 3

    first = get_json(f"/music/netease/playlist?id={pid}&offset=0&limit=100&account=qingqing")
    second = get_json(f"/music/netease/playlist?id={pid}&offset=100&limit=100&account=qingqing")

    first_returned = int(first.get("returned") or len(first.get("songs") or []))
    second_returned = int(second.get("returned") or len(second.get("songs") or []))

    print(f"overlay_injected={str(injected).lower()}")
    print(f"overlay_asset_page100={str(js_has_page_100).lower()}")
    print(f"overlay_asset_observer={str(js_has_observer).lower()}")
    print(f"overlay_asset_renderer={str(js_has_lazy_renderer).lower()}")
    print(f"account_ui_key={str(js_has_account_key).lower()}")
    print(f"account_ui_router={str(js_has_account_router).lower()}")
    print(f"account_ui_labels={str(js_has_account_labels).lower()}")
    print(f"account_ui_bar={str(js_has_account_bar).lower()}")
    print(f"account_ui_hot_switch={str(js_has_hot_switch).lower()}")
    print(f"account_ui_warm_cache={str(js_has_warm_cache).lower()}")
    print(f"account_slots_present={str(has_two_slots).lower()}")
    print(
        f"page1_returned={first_returned} page1_more={str(bool(first.get('more'))).lower()} "
        f"page1_next={first.get('nextOffset')}"
    )
    print(
        f"page2_returned={second_returned} page2_offset={second.get('offset')} "
        f"availableTotal={second.get('availableTotal')} expected={expected}"
    )
    print("private fields printed: 0")
    print("writes performed: 0")

    ok = all([
        injected,
        js_has_page_100,
        js_has_observer,
        js_has_lazy_renderer,
        js_has_account_key,
        js_has_account_router,
        js_has_account_labels,
        js_has_account_bar,
        js_has_hot_switch,
        js_has_warm_cache,
        has_two_slots,
        first.get("ok"),
        second.get("ok"),
        first_returned == min(100, expected),
        int(second.get("offset") or -1) == 100,
    ])
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
