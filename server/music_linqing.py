#!/usr/bin/env python3
"""Linqing music server overlay.

Keeps upstream ``server/music.py`` intact and overrides only the pieces we need.
Current change: rebuild complete NetEase playlists from the full ``trackIds`` list
and expose page-based reads via ``offset`` + ``limit``.

Run:
    python3 server/music_linqing.py

The same ``server/.netease_cred``, ``server/.secret`` and ``server/data`` are reused.
"""

from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from music import MusicHandler, ServerState, HERE, logger


class LinqingMusicHandler(MusicHandler):
    """Small overlay on the upstream request handler."""

    NETEASE_DETAIL_BATCH = 100
    NETEASE_PLAYLIST_PAGE_MAX = 500

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
    logger.info(
        "Netease cookie: %s",
        "configured" if (HERE / ".netease_cred").exists() else "NOT FOUND",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
