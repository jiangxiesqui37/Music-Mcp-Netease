/* Linqing client overlay: full NetEase playlist paging + two-person account switcher.
 * Keeps upstream's large single-file client intact. The Linqing server injects this
 * file after the upstream scripts load, so we can reuse the existing renderer/actions.
 */
(() => {
  'use strict';

  const PAGE_SIZE = 100;
  const ACCOUNT_KEY = 'linqing-music-account';
  const ACCOUNT_LABELS = { qingqing: '卿卿', linlin: '老公' };
  const ACCOUNT_CACHE_TTL = 5 * 60 * 1000;
  const originalRenderPlaylistDetail = renderPlaylistDetail;
  const baseApi = api;

  let selectedAccount = readSavedAccount() || 'qingqing';
  let observer = null;
  let fallbackRoot = null;
  let fallbackScroll = null;
  let session = 0;
  let switchingAccount = false;
  let accountData = null;
  const accountReadCache = new Map();

  function readSavedAccount() {
    try { return localStorage.getItem(ACCOUNT_KEY) || ''; } catch { return ''; }
  }

  function saveAccount(account) {
    try { localStorage.setItem(ACCOUNT_KEY, account); } catch {}
  }

  function scopedPathFor(account, path) {
    if (typeof path !== 'string' || !path.startsWith('/music/')) return path;
    if (path.startsWith('/music/linqing/accounts')) return path;
    try {
      const u = new URL(path, location.origin);
      u.searchParams.set('account', account);
      return u.pathname + u.search + u.hash;
    } catch {
      const sep = path.includes('?') ? '&' : '?';
      return path + sep + 'account=' + encodeURIComponent(account);
    }
  }

  function cacheableAccountRead(path) {
    if (typeof path !== 'string') return false;
    return path === '/music/netease/likes'
      || path === '/music/netease/playlists';
  }

  function cacheKey(account, path) {
    return account + '|' + path;
  }

  function invalidateAccountCache(account) {
    const prefix = account + '|';
    for (const key of accountReadCache.keys()) {
      if (key.startsWith(prefix)) accountReadCache.delete(key);
    }
  }

  async function cachedAccountRead(account, path, opts) {
    const key = cacheKey(account, path);
    const hit = accountReadCache.get(key);
    if (hit && Date.now() - hit.at < ACCOUNT_CACHE_TTL) return hit.value;
    const value = await baseApi(scopedPathFor(account, path), opts);
    accountReadCache.set(key, { at: Date.now(), value });
    return value;
  }

  async function accountApi(path, opts) {
    const method = String(opts?.method || 'GET').toUpperCase();
    if (method === 'GET' && cacheableAccountRead(path)) {
      return cachedAccountRead(selectedAccount, path, opts);
    }
    if (method !== 'GET') invalidateAccountCache(selectedAccount);
    return baseApi(scopedPathFor(selectedAccount, path), opts);
  }

  async function prefetchAccountCore(account) {
    await Promise.allSettled([
      cachedAccountRead(account, '/music/netease/likes'),
      cachedAccountRead(account, '/music/netease/playlists'),
    ]);
  }

  // Route all later player requests through the selected account. apiPost() in the
  // upstream client calls api(), so POSTs such as like/scrobble follow this too.
  api = accountApi;

  function installAccountStyles() {
    if (document.getElementById('linqing-account-style')) return;
    const style = document.createElement('style');
    style.id = 'linqing-account-style';
    style.textContent = `
      .linqing-account-bar{
        display:flex; align-items:center; justify-content:center; gap:6px;
        padding:6px 12px 2px; flex-shrink:0;
        background:rgba(255,250,252,.42);
      }
      .linqing-account-pill{
        border:1px solid rgba(255,255,255,.92); border-radius:999px;
        background:rgba(255,255,255,.48); color:var(--ink-soft);
        min-width:72px; padding:6px 12px; font-size:11px; font-weight:600;
        letter-spacing:.03em; box-shadow:0 3px 10px rgba(150,90,110,.07);
        transition:transform var(--t-fast) var(--ease-spring),
                   background var(--t-fast) var(--ease-out),
                   color var(--t-fast) var(--ease-out), opacity var(--t-fast) var(--ease-out);
      }
      .linqing-account-pill:active:not(:disabled){ transform:scale(.96); }
      .linqing-account-pill.active{
        color:var(--rose-deep); background:rgba(194,112,138,.14);
        box-shadow:inset 0 0 0 1px rgba(194,112,138,.08), 0 3px 10px rgba(150,90,110,.08);
      }
      .linqing-account-pill:disabled{ opacity:.38; cursor:default; }
      .linqing-account-pill.busy{ opacity:.62; }
      .linqing-account-pill .slot-dot{
        display:inline-block; width:5px; height:5px; margin-left:5px;
        border-radius:50%; background:currentColor; vertical-align:1px; opacity:.55;
      }
    `;
    document.head.appendChild(style);
  }

  function updateAccountBarState() {
    const configured = new Set(
      (accountData?.accounts || []).filter(x => x.configured).map(x => String(x.id))
    );
    document.querySelectorAll('.linqing-account-pill').forEach(btn => {
      const id = String(btn.dataset.account || '');
      btn.classList.toggle('active', id === selectedAccount);
      btn.classList.toggle('busy', switchingAccount);
      btn.disabled = switchingAccount || !configured.has(id);
    });
  }

  async function linqingHotAccountSwitch(id) {
    if (switchingAccount || id === selectedAccount) return;
    const slot = (accountData?.accounts || []).find(x => String(x.id) === id);
    if (!slot?.configured) return;

    switchingAccount = true;
    updateAccountBarState();

    try {
      // Warm the two slow account reads while the current screen stays visible.
      // Once they arrive, swap state in place instead of reloading the document.
      await prefetchAccountCore(id);

      cleanupLazyLoader();
      session += 1;
      selectedAccount = id;
      saveAccount(id);

      playlistDetail = null;
      if (Array.isArray(plDetailSongs)) plDetailSongs.length = 0;
      if (Array.isArray(neteaseLists)) neteaseLists.length = 0;
      if (likedIds?.clear) likedIds.clear();

      if (typeof loadLikedIds === 'function') await loadLikedIds();
      if (typeof updateNavActive === 'function') updateNavActive({ silent: true });
      if (typeof renderTab === 'function') renderTab();
      if (typeof renderNpBar === 'function') renderNpBar();
    } catch (err) {
      console.warn('linqing account switch failed', err);
    } finally {
      switchingAccount = false;
      updateAccountBarState();
    }
  }

  function renderAccountBar(data) {
    installAccountStyles();
    accountData = data;
    const topbar = document.querySelector('#app > .topbar');
    if (!topbar) return false;

    document.getElementById('linqing-account-bar')?.remove();
    const bar = document.createElement('div');
    bar.id = 'linqing-account-bar';
    bar.className = 'linqing-account-bar';

    for (const slot of data.accounts || []) {
      const id = String(slot.id || '');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'linqing-account-pill' + (id === selectedAccount ? ' active' : '');
      btn.dataset.account = id;
      btn.disabled = !slot.configured;
      btn.title = slot.configured ? `切到${ACCOUNT_LABELS[id] || id}` : `${ACCOUNT_LABELS[id] || id}账号待入住`;
      btn.innerHTML = `${ACCOUNT_LABELS[id] || id}${slot.configured ? '' : '<span class="slot-dot"></span>'}`;
      btn.addEventListener('click', () => linqingHotAccountSwitch(id));
      bar.appendChild(btn);
    }

    topbar.insertAdjacentElement('afterend', bar);
    return true;
  }

  async function initAccountSwitcher() {
    let data = null;
    try {
      data = await baseApi('/music/linqing/accounts');
    } catch (err) {
      console.warn('linqing account slots unavailable', err);
      return;
    }
    if (!data?.ok) return;
    accountData = data;

    const configured = new Set(
      (data.accounts || []).filter(x => x.configured).map(x => String(x.id))
    );
    if (!configured.has(selectedAccount)) {
      selectedAccount = configured.has(String(data.default))
        ? String(data.default)
        : ((data.accounts || []).find(x => x.configured)?.id || 'qingqing');
      saveAccount(selectedAccount);
    }

    if (!renderAccountBar(data)) {
      const mo = new MutationObserver(() => {
        if (renderAccountBar(data)) mo.disconnect();
      });
      mo.observe(document.getElementById('app') || document.body, { childList: true, subtree: true });
    }

    // Upstream boot runs before this injected overlay. Rebuild the heart ledger for
    // the selected account, then quietly warm the other configured account in back.
    try {
      if (likedIds?.clear) likedIds.clear();
      if (typeof loadLikedIds === 'function') await loadLikedIds();
      if (typeof renderTab === 'function') renderTab();
    } catch (err) {
      console.warn('linqing account refresh failed', err);
    }

    for (const id of configured) {
      if (id !== selectedAccount) prefetchAccountCore(id).catch(() => {});
    }
  }

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
      invalidateAccountCache(selectedAccount);
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
    // turn that request into the first 100-song page, then restore account routing.
    api = async function linqingFirstPageApi(path, opts) {
      if (
        typeof path === 'string'
        && path.startsWith('/music/netease/playlist?')
        && path.includes('id=' + encodeURIComponent(pl.id))
      ) {
        firstPage = await accountApi(playlistUrl(pl, 0), opts);
        return firstPage;
      }
      return accountApi(path, opts);
    };

    try {
      await originalRenderPlaylistDetail(container);
    } finally {
      api = accountApi;
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
        const page = await accountApi(playlistUrl(pl, nextOffset));
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

  initAccountSwitcher();
})();