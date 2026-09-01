/* Linqing client overlay: lazy-load complete NetEase playlists without touching
 * upstream's large single-file client. The server injects this file after the
 * upstream scripts have loaded, so we can reuse its existing renderer/actions.
 */
(() => {
  'use strict';

  const PAGE_SIZE = 100;
  const originalRenderPlaylistDetail = renderPlaylistDetail;
  const originalApi = api;

  let observer = null;
  let fallbackRoot = null;
  let fallbackScroll = null;
  let session = 0;

  function cleanupLazyLoader() {
    if (observer) observer.disconnect();
    observer = null;
    if (fallbackRoot && fallbackScroll) {
      fallbackRoot.removeEventListener('scroll', fallbackScroll);
    }
    fallbackRoot = null;
    fallbackScroll = null;
  }

  function playlistUrl(pl, offset) {
    return '/music/netease/playlist?id=' + encodeURIComponent(pl.id)
      + '&offset=' + offset + '&limit=' + PAGE_SIZE;
  }

  function makeSongRow(s, index) {
    const isActive = state.song?.songId === s.songId;
    const liked = !!(s.songId && likedIds.has(s.songId));
    return `
      <div class="song-row ${isActive ? 'active' : ''}" data-idx="${index}" data-linqing-lazy="1">
        <div class="song-idx">${index + 1}</div>
        ${coverBox(s.cover, 'song-cover')}
        <div class="song-info">
          <div class="song-name">${esc(s.name)}</div>
          <div class="song-artist">${esc(s.artist)}${s.addedBy ? ' · ' + esc(s.addedBy) : ''}</div>
        </div>
        <button class="like-btn song-like${liked ? ' liked' : ''}" data-like="${index}" aria-label="收藏">${heartSvg(liked)}</button>
      </div>`;
  }

  function bindLazyRow(row) {
    const index = Number(row.dataset.idx);
    row.querySelector('.song-cover')?.addEventListener('click', () => {
      playSong(plDetailSongs[index], plDetailSongs.slice(index + 1));
    });
    row.querySelector('.song-info')?.addEventListener('click', () => {
      playSong(plDetailSongs[index], plDetailSongs.slice(index + 1));
    });
    row.querySelector('.song-like')?.addEventListener('click', async e => {
      e.stopPropagation();
      const song = plDetailSongs[index];
      if (!song) return;
      await likeSong(song);
      const liked = likedIds.has(song.songId);
      const btn = e.currentTarget;
      btn.classList.toggle('liked', liked);
      btn.innerHTML = heartSvg(liked);
    });
  }

  function updateProgress(container, loaded, total, sentinel) {
    const meta = container.querySelector('.pl-hero-meta');
    if (meta) meta.textContent = `网易云 ☁ ${loaded}/${total} songs`;
    if (sentinel) {
      sentinel.textContent = loaded < total
        ? `${loaded} / ${total} · 往下滑继续加载`
        : `${total} / ${total} · 已全部加载`;
    }
  }

  function makeSentinel() {
    const el = document.createElement('div');
    el.className = 'linqing-load-sentinel';
    el.setAttribute('aria-live', 'polite');
    Object.assign(el.style, {
      padding: '18px 12px 22px',
      textAlign: 'center',
      fontSize: '11px',
      color: 'var(--muted)',
      letterSpacing: '.02em'
    });
    return el;
  }

  function observeSentinel(root, sentinel, loadMore) {
    if ('IntersectionObserver' in window) {
      observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) loadMore();
      }, {
        root,
        rootMargin: '420px 0px 420px 0px',
        threshold: 0.01
      });
      observer.observe(sentinel);
      return;
    }

    fallbackRoot = root || document.scrollingElement || document.documentElement;
    fallbackScroll = () => {
      const rect = sentinel.getBoundingClientRect();
      if (rect.top < window.innerHeight + 420) loadMore();
    };
    fallbackRoot.addEventListener('scroll', fallbackScroll, { passive: true });
    fallbackScroll();
  }

  renderPlaylistDetail = async function linqingRenderPlaylistDetail(container) {
    cleanupLazyLoader();
    const mySession = ++session;
    const pl = playlistDetail;

    if (!pl?.netease) {
      return originalRenderPlaylistDetail(container);
    }

    let firstPage = null;

    // Upstream currently asks for limit=500. During this one render, transparently
    // turn that request into the first 100-song page, then give control back.
    api = async function linqingFirstPageApi(path, opts) {
      if (
        typeof path === 'string'
        && path.startsWith('/music/netease/playlist?')
        && path.includes('id=' + encodeURIComponent(pl.id))
      ) {
        firstPage = await originalApi(playlistUrl(pl, 0), opts);
        return firstPage;
      }
      return originalApi(path, opts);
    };

    try {
      await originalRenderPlaylistDetail(container);
    } finally {
      api = originalApi;
    }

    if (
      mySession !== session
      || playlistDetail !== pl
      || !firstPage?.ok
      || !firstPage.more
    ) {
      return;
    }

    const list = container.querySelector('.song-list');
    if (!list) return;

    let nextOffset = Number(firstPage.nextOffset ?? plDetailSongs.length);
    const total = Number(firstPage.availableTotal ?? firstPage.total ?? pl.count ?? plDetailSongs.length);
    let loading = false;
    let more = !!firstPage.more;
    const sentinel = makeSentinel();
    list.appendChild(sentinel);
    updateProgress(container, plDetailSongs.length, total, sentinel);

    const loadMore = async () => {
      if (loading || !more || mySession !== session || playlistDetail !== pl) return;
      loading = true;
      sentinel.textContent = `${plDetailSongs.length} / ${total} · 加载中…`;

      try {
        const page = await originalApi(playlistUrl(pl, nextOffset));
        if (mySession !== session || playlistDetail !== pl) return;
        if (!page?.ok) throw new Error('playlist page failed');

        const songs = page.songs || [];
        const startIndex = plDetailSongs.length;
        plDetailSongs.push(...songs);

        if (songs.length) {
          sentinel.insertAdjacentHTML(
            'beforebegin',
            songs.map((song, i) => makeSongRow(song, startIndex + i)).join('')
          );
          for (let i = startIndex; i < plDetailSongs.length; i += 1) {
            const row = list.querySelector(`.song-row[data-idx="${i}"][data-linqing-lazy="1"]`);
            if (row) bindLazyRow(row);
          }
        }

        nextOffset = Number(page.nextOffset ?? (nextOffset + songs.length));
        more = !!page.more;
        updateProgress(container, plDetailSongs.length, total, sentinel);

        if (!more) {
          cleanupLazyLoader();
          // Leave the final count visible for a moment instead of making the list jump.
          setTimeout(() => {
            if (sentinel.isConnected) sentinel.remove();
          }, 1800);
        }
      } catch (err) {
        console.warn('linqing lazy playlist page failed', err);
        sentinel.textContent = `${plDetailSongs.length} / ${total} · 加载失败，继续下滑重试`;
      } finally {
        loading = false;
      }
    };

    const root = container.classList.contains('tab-body')
      ? container
      : container.closest('.tab-body');
    observeSentinel(root || null, sentinel, loadMore);
  };
})();
