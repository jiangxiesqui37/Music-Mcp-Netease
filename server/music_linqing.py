#!/usr/bin/env python3
"""Linqing music server overlay.

Keeps upstream ``server/music.py`` intact and overrides only the pieces we need.
Current changes:
- rebuild complete NetEase playlists from the full ``trackIds`` list
- expose page-based reads via ``offset`` + ``limit``
- inject the small Linqing client overlay that lazy-loads those pages
- add two local NetEase account slots without committing credentials

Run:
    python3 server/music_linqing.py

Credential layout:
    server/.netease_accounts/qingqing.cred
    server/.netease_accounts/linlin.cred

Each file is one line: ``MUSIC_U=<value>`` and should be mode 600.
For backward compatibility, the default ``qingqing`` slot also falls back to
``server/.netease_cred`` while we migrate the existing account.
"""

from __future__ import annotations

import os
import re
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from music import MusicHandler, ServerState, HERE, logger


class LinqingMusicHandler(MusicHandler):
    """Small overlay on the upstream request handler."""

    NETEASE_DETAIL_BATCH = 100
    NETEASE_PLAYLIST_PAGE_MAX = 500
    CLIENT_OVERLAY_TAG = '<script src="/linqing-pagination.js"></script>'

    ACCOUNT_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
    ACCOUNT_IDS = tuple(
        x.strip()
        for x in os.environ.get("LINQING_ACCOUNT_IDS", "qingqing,linlin").split(",")
        if x.strip()
    ) or ("qingqing", "linlin")
    DEFAULT_ACCOUNT = os.environ.get("LINQING_DEFAULT_ACCOUNT", ACCOUNT_IDS[0]).strip() or ACCOUNT_IDS[0]
    if DEFAULT_ACCOUNT not in ACCOUNT_IDS:
        DEFAULT_ACCOUNT = ACCOUNT_IDS[0]

    def _serve_static(self, path: str):
        """Serve upstream client, injecting our tiny JS overlay into index.html only."""
        if path not in {"/", "/index.html"}:
            return super()._serve_static(path)

        target = HERE.parent / "client" / "index.html"
        try:
            html = target.read_text(encoding="utf-8")
        except OSError:
            return super()._serve_static(path)

        if self.CLIENT_OVERLAY_TAG not in html:
            if "</body>" in html:
                html = html.replace(
                    "</body>",
                    self.CLIENT_OVERLAY_TAG + "\n</body>",
                    1,
                )
            else:
                html += "\n" + self.CLIENT_OVERLAY_TAG + "\n"

        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ── Linqing account slots ────────────────────────────────────────────────

    def _requested_account(self) -> str:
        """Resolve a request-scoped account slot from ``?account=...``."""
        qs = parse_qs(urlparse(self.path).query)
        requested = (qs.get("account") or [self.DEFAULT_ACCOUNT])[0].strip().lower()
        if not self.ACCOUNT_RE.fullmatch(requested):
            return self.DEFAULT_ACCOUNT
        if requested not in self.ACCOUNT_IDS:
            return self.DEFAULT_ACCOUNT
        return requested

    def _account_cred_path(self, account: str):
        return HERE / ".netease_accounts" / f"{account}.cred"

    @staticmethod
    def _read_music_u_file(path) -> str:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("MUSIC_U="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value
        except OSError:
            pass
        return ""

    def _music_u_for_account(self, account: str) -> str:
        value = self._read_music_u_file(self._account_cred_path(account))
        if value:
            return value
        # Keep the existing account working until its credential is moved into the
        # new slot directory. Only the default slot may use this legacy fallback.
        if account == self.DEFAULT_ACCOUNT:
            return self._read_music_u_file(HERE / ".netease_cred")
        return ""

    def _netease_cookie(self) -> str:
        """Override upstream cookie lookup with request-scoped account routing."""
        value = self._music_u_for_account(self._requested_account())
        return f"MUSIC_U={value}" if value else ""

    def _handle_linqing_accounts(self):
        accounts = []
        for account in self.ACCOUNT_IDS:
            accounts.append({
                "id": account,
                "configured": bool(self._music_u_for_account(account)),
                "default": account == self.DEFAULT_ACCOUNT,
            })
        self._send_json(200, {
            "ok": True,
            "default": self.DEFAULT_ACCOUNT,
            "accounts": accounts,
        })

    def do_GET(self):
        if urlparse(self.path).path == "/music/linqing/accounts":
            if not self._require_auth():
                return
            self._handle_linqing_accounts()
            return
        return super().do_GET()

    # ── Full NetEase playlist paging ────────────────────────────────────────

    @staticmethod
    def _normalize_netease_song(track: dict) -> dict:
        """Normalize both old ``song/detail`` and v6 playlist field names."""
        artists = track.get("ar") or track.get("artists") or []
        album = track.get("al") or track.get("album") or {}
        cover = str(album.get("picUrl") or "").replace("http://", "https://", 1)
        return {
            "songId": track.get("id"),
            "name": track.get("name", ""),
            "artist": ", ".join(
                a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")
            ),
            "album": album.get("name", ""),
            "cover": cover,
        }

    def _resolve_netease_song_ids(self, song_ids: list[int]) -> list[dict]:
        """Resolve IDs in bounded batches while preserving playlist order."""
        out: list[dict] = []
        for start in range(0, len(song_ids), self.NETEASE_DETAIL_BATCH):
            chunk = song_ids[start:start + self.NETEASE_DETAIL_BATCH]
            if not chunk:
                continue
            joined = ",".join(str(x) for x in chunk)
            detail = self._netease_request(
                f"https://music.163.com/api/song/detail?ids=[{joined}]",
                timeout=20,
            )
            resolved = {}
            for track in detail.get("songs") or []:
                try:
                    resolved[int(track.get("id"))] = track
                except (TypeError, ValueError):
                    continue
            for sid in chunk:
                track = resolved.get(int(sid))
                if track:
                    out.append(self._normalize_netease_song(track))
        return out

    def _handle_netease_playlist(self):
        """Read any account playlist without the 20/500/1000 track truncation.

        NetEase's playlist-detail response exposes a complete ``trackIds`` array even
        when ``tracks`` itself is truncated. We page over that ID list, then resolve
        only the requested page through ``song/detail``.
        """
        qs = parse_qs(urlparse(self.path).query)
        pid = qs.get("id", [""])[0]
        if not pid.isdigit():
            self._send_json(400, {"error": "missing or bad id"})
            return

        try:
            offset = max(0, int(qs.get("offset", ["0"])[0] or 0))
        except ValueError:
            offset = 0
        try:
            limit = min(
                self.NETEASE_PLAYLIST_PAGE_MAX,
                max(1, int(qs.get("limit", ["100"])[0] or 100)),
            )
        except ValueError:
            limit = 100

        try:
            detail = self._netease_request(
                f"https://music.163.com/api/v6/playlist/detail?id={pid}&n=1000&s=0",
                timeout=20,
            )
            playlist = detail.get("playlist") or {}
            track_ids = []
            for row in playlist.get("trackIds") or []:
                if not isinstance(row, dict):
                    continue
                sid = row.get("id")
                try:
                    track_ids.append(int(sid))
                except (TypeError, ValueError):
                    continue

            # Small/odd playlists may omit trackIds; retain upstream-compatible fallback.
            if not track_ids:
                tracks = playlist.get("tracks") or []
                track_ids = [int(t["id"]) for t in tracks if isinstance(t, dict) and t.get("id")]

            page_ids = track_ids[offset:offset + limit]
            songs = self._resolve_netease_song_ids(page_ids)
        except Exception as exc:
            logger.warning("full playlist read failed pid=%s offset=%s: %s", pid, offset, exc)
            self._send_json(502, {"error": "歌单内容拉取失败"})
            return

        total = int(playlist.get("trackCount") or len(track_ids))
        available_total = len(track_ids)
        next_offset = offset + len(page_ids)
        self._send_json(200, {
            "ok": True,
            "id": playlist.get("id"),
            "name": playlist.get("name", ""),
            "total": total,
            "availableTotal": available_total,
            "offset": offset,
            "limit": limit,
            "returned": len(songs),
            "more": next_offset < available_total,
            "nextOffset": next_offset if next_offset < available_total else None,
            "songs": songs,
        })


def main():
    port = int(os.environ.get("PORT", "9090"))
    state = ServerState(port)
    LinqingMusicHandler.state = state

    server = ThreadingHTTPServer((state.host, state.port), LinqingMusicHandler)
    logger.info("music-linqing starting on %s:%d", state.host, state.port)
    logger.info("Data dir: %s", state.data_dir)
    logger.info("Netease account slots: %s", ",".join(LinqingMusicHandler.ACCOUNT_IDS))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
