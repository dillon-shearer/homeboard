(function (root) {
  "use strict";

  const DEFAULTS = { interval_s: 20, transition: true, fit: "contain", shuffle: false, captions: false };
  const MAX_BACKOFF_S = 30;

  function applySettings(prev, incoming) {
    const base = Object.assign({}, DEFAULTS, prev || {});
    const s = incoming || {};
    const out = Object.assign({}, base);
    if (typeof s.interval_s === "number" && s.interval_s > 0) out.interval_s = s.interval_s;
    if ("transition" in s) out.transition = !!s.transition;
    if ("fit" in s) out.fit = s.fit === "cover" ? "cover" : "contain";
    if ("shuffle" in s) out.shuffle = !!s.shuffle;
    if ("captions" in s) out.captions = !!s.captions;
    return out;
  }

  function shuffleIds(ids, avoidFirst, rand) {
    rand = rand || Math.random;
    const out = ids.slice();
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      const t = out[i];
      out[i] = out[j];
      out[j] = t;
    }
    if (out.length >= 2 && out[0] === avoidFirst) {
      const j = 1 + Math.floor(rand() * (out.length - 1));
      out[0] = out[j];
      out[j] = avoidFirst;
    }
    return out;
  }

  function buildOrder(ids, shuffle, lastId, rand) {
    return shuffle ? shuffleIds(ids, lastId, rand) : ids.slice();
  }

  function nextIndex(order, pos, ids, shuffle, rand) {
    if (order.length && pos + 1 < order.length) return { order: order, pos: pos + 1 };
    const lastId = order.length ? order[order.length - 1] : null;
    return { order: buildOrder(ids, shuffle, lastId, rand), pos: 0 };
  }

  function mergeList(oldOrder, newIds, currentId) {
    if (currentId === null || currentId === undefined) {
      return { id: newIds.length ? newIds[0] : null, removed: false };
    }
    if (newIds.indexOf(currentId) !== -1) return { id: currentId, removed: false };
    const start = oldOrder.indexOf(currentId);
    if (start !== -1) {
      for (let k = 1; k < oldOrder.length; k++) {
        const id = oldOrder[(start + k) % oldOrder.length];
        if (newIds.indexOf(id) !== -1) return { id: id, removed: true };
      }
    }
    return { id: newIds.length ? newIds[0] : null, removed: true };
  }

  function orderAround(ids, currentId, shuffle, rand) {
    if (currentId === null || ids.indexOf(currentId) === -1) {
      return { order: buildOrder(ids, shuffle, null, rand), pos: -1 };
    }
    if (shuffle) {
      const rest = ids.filter(function (id) { return id !== currentId; });
      return { order: [currentId].concat(shuffleIds(rest, null, rand)), pos: 0 };
    }
    return { order: ids.slice(), pos: ids.indexOf(currentId) };
  }

  function resumeOrder(ids, resumeId, shuffle, rand) {
    const o = orderAround(ids, resumeId === undefined ? null : resumeId, shuffle, rand);
    if (resumeId !== null && resumeId !== undefined && ids.indexOf(resumeId) !== -1) o.pos -= 1;
    return o;
  }

  function nextBackoff(seconds) {
    return Math.min(seconds * 2, MAX_BACKOFF_S);
  }

  function formatClock(d) {
    const h = d.getHours() % 12 || 12;
    const m = String(d.getMinutes()).padStart(2, "0");
    return h + ":" + m + " " + (d.getHours() < 12 ? "am" : "pm");
  }

  const Slideshow = {
    DEFAULTS: DEFAULTS,
    applySettings: applySettings,
    shuffleIds: shuffleIds,
    buildOrder: buildOrder,
    nextIndex: nextIndex,
    mergeList: mergeList,
    orderAround: orderAround,
    resumeOrder: resumeOrder,
    nextBackoff: nextBackoff,
    formatClock: formatClock,
  };

  root.Slideshow = Slideshow;
  if (typeof module !== "undefined" && module.exports) module.exports = Slideshow;
  if (typeof document === "undefined") return;

  const RETRY_MS = 5000;
  const LOAD_TIMEOUT_MS = 15000;
  const CAPTION_MS = 5000;

  const layerA = document.getElementById("layer-a");
  const layerB = document.getElementById("layer-b");
  const captionEl = document.getElementById("caption");
  const clockEl = document.getElementById("clock");

  const state = {
    photos: [],
    byId: {},
    order: [],
    pos: -1,
    settings: applySettings(null, null),
    currentId: null,
    resumeId: null,
    reportedId: null,
    shownAt: 0,
    timer: null,
    upcoming: null,
    loading: false,
    showToken: 0,
    needPhotos: true,
    needSettings: true,
    needEmpty: false,
    front: layerA,
    back: layerB,
  };
  let captionTimer = null;
  let backoff = 1;

  function ids() {
    return state.photos.map(function (p) { return p.id; });
  }

  function fetchJson(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }

  function transitionMs() {
    return state.settings.transition ? 1000 : 0;
  }

  function schedule(delay) {
    clearTimeout(state.timer);
    if (delay === undefined) {
      const interval = state.settings.interval_s * 1000;
      if (!state.order.length) delay = Math.min(RETRY_MS, interval);
      else delay = Math.max(0, state.shownAt + interval - Date.now());
    }
    state.timer = setTimeout(tick, delay);
  }

  function applyDom() {
    [layerA, layerB].forEach(function (l) {
      l.classList.toggle("cover", state.settings.fit === "cover");
      l.classList.toggle("instant", !state.settings.transition);
    });
    if (!state.settings.captions) hideCaption();
  }

  function setSettings(data) {
    const prev = state.settings;
    state.settings = applySettings(prev, data);
    applyDom();
    if (prev.shuffle !== state.settings.shuffle && state.order.length) {
      const o = state.currentId === null
        ? resumeOrder(ids(), state.resumeId, state.settings.shuffle)
        : orderAround(ids(), state.currentId, state.settings.shuffle);
      state.order = o.order;
      state.pos = o.pos;
      state.upcoming = null;
    }
    if (prev.interval_s !== state.settings.interval_s && state.currentId !== null && !state.loading) {
      schedule();
    }
  }

  function loadSettings() {
    return fetchJson("/api/settings").then(function (data) {
      state.needSettings = false;
      setSettings(data);
    }, function () {
      state.needSettings = true;
    });
  }

  function setPhotos(list) {
    const hadCurrent = state.currentId !== null;
    const newIds = list.map(function (p) { return p.id; });
    const m = mergeList(state.order, newIds, state.currentId);
    state.photos = list;
    state.byId = {};
    list.forEach(function (p) { state.byId[p.id] = p; });
    state.upcoming = null;
    if (!newIds.length) {
      state.order = [];
      state.pos = -1;
      state.currentId = null;
      showEmpty();
      return { restart: false };
    }
    hideEmpty();
    const o = hadCurrent
      ? orderAround(newIds, m.id, state.settings.shuffle)
      : resumeOrder(newIds, state.resumeId, state.settings.shuffle);
    state.order = o.order;
    state.pos = o.pos;
    if (m.removed) state.pos -= 1;
    return { restart: m.removed || !hadCurrent };
  }

  function loadPhotos() {
    return fetchJson("/api/photos").then(function (list) {
      state.needPhotos = false;
      return setPhotos(list);
    }, function () {
      state.needPhotos = true;
      return null;
    });
  }

  function loadResume() {
    return fetchJson("/api/display/current").then(function (data) {
      if (data && typeof data.id === "number") {
        state.resumeId = data.id;
        state.reportedId = data.id;
      }
    }, function () {});
  }

  function reportCurrent(id) {
    if (id === state.reportedId) return;
    state.reportedId = id;
    fetch("/api/display/current", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id }),
    }).catch(function () {});
  }

  function emptyEl() {
    return document.getElementById("empty");
  }

  function showEmpty() {
    state.showToken++;
    state.loading = false;
    [layerA, layerB].forEach(function (l) {
      l.hidden = true;
      l.classList.remove("front");
    });
    hideCaption();
    const el = emptyEl();
    if (el) {
      el.hidden = false;
      return;
    }
    state.needEmpty = true;
    fetch("/", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      const found = new DOMParser().parseFromString(html, "text/html").getElementById("empty");
      if (!found || emptyEl()) return;
      state.needEmpty = false;
      const node = document.importNode(found, true);
      node.hidden = state.photos.length > 0;
      document.body.insertBefore(node, captionEl);
    }).catch(function () {});
  }

  function hideEmpty() {
    state.needEmpty = false;
    const el = emptyEl();
    if (el) el.hidden = true;
    layerA.hidden = false;
    layerB.hidden = false;
  }

  function hideCaption() {
    clearTimeout(captionTimer);
    captionEl.classList.remove("show");
  }

  function showCaption(photo) {
    hideCaption();
    if (!state.settings.captions || !photo.caption) return;
    captionEl.textContent = photo.caption;
    captionEl.classList.add("show");
    captionTimer = setTimeout(hideCaption, CAPTION_MS);
  }

  function preload(token) {
    if (token !== state.showToken || !state.order.length) return;
    state.upcoming = nextIndex(state.order, state.pos, ids(), state.settings.shuffle);
    const id = state.upcoming.order[state.upcoming.pos];
    const photo = state.byId[id];
    if (!photo || id === state.currentId) return;
    const layer = state.back;
    layer.onload = layer.onerror = null;
    layer.dataset.id = String(id);
    layer.src = photo.url;
  }

  function swap(layer, photo, token) {
    state.back = state.front;
    state.front = layer;
    layer.classList.add("front");
    state.back.classList.remove("front");
    state.currentId = photo.id;
    state.shownAt = Date.now();
    state.resumeId = null;
    reportCurrent(photo.id);
    showCaption(photo);
    schedule();
    setTimeout(function () { preload(token); }, transitionMs() + 50);
  }

  function show(id) {
    const photo = state.byId[id];
    if (!photo) {
      schedule(RETRY_MS);
      return;
    }
    const token = ++state.showToken;
    const layer = state.back;
    let done = false;
    let watchdog = null;
    state.loading = true;

    function finish(ok) {
      if (done || token !== state.showToken) return;
      done = true;
      clearTimeout(watchdog);
      layer.onload = layer.onerror = null;
      state.loading = false;
      if (ok) {
        swap(layer, photo, token);
      } else {
        delete layer.dataset.id;
        state.needPhotos = true;
        schedule(RETRY_MS);
      }
    }

    const same = layer.dataset.id === String(id) && layer.getAttribute("src") === photo.url;
    if (same && layer.complete && layer.naturalWidth) {
      finish(true);
      return;
    }
    layer.onload = function () { finish(true); };
    layer.onerror = function () { finish(false); };
    watchdog = setTimeout(function () { finish(false); }, LOAD_TIMEOUT_MS);
    if (same && !layer.complete) return;
    layer.dataset.id = String(id);
    if (layer.getAttribute("src") === photo.url) layer.removeAttribute("src");
    layer.src = photo.url;
  }

  function advance() {
    if (!state.order.length) {
      schedule();
      return;
    }
    const next = state.upcoming || nextIndex(state.order, state.pos, ids(), state.settings.shuffle);
    state.upcoming = null;
    state.order = next.order;
    state.pos = next.pos;
    const id = state.order[state.pos];
    if (id === state.currentId && state.front.dataset.id === String(id)) {
      state.shownAt = Date.now();
      showCaption(state.byId[id]);
      schedule();
      return;
    }
    show(id);
  }

  function tick() {
    clearTimeout(state.timer);
    state.timer = null;
    if (state.needSettings) loadSettings();
    if (state.needEmpty && !state.photos.length) showEmpty();
    const wait = state.needPhotos ? loadPhotos() : Promise.resolve();
    wait.then(advance);
  }

  function onPhotosChanged() {
    loadPhotos().then(function (r) {
      if (r && r.restart) schedule(0);
    });
  }

  function onSettingsChanged() {
    loadSettings();
  }

  function connect() {
    let es;
    try {
      es = new EventSource("/api/events");
    } catch (e) {
      setTimeout(connect, backoff * 1000);
      backoff = nextBackoff(backoff);
      return;
    }
    es.onopen = function () { backoff = 1; };
    es.addEventListener("hello", function () {
      onSettingsChanged();
      onPhotosChanged();
    });
    es.addEventListener("photos_changed", onPhotosChanged);
    es.addEventListener("settings_changed", onSettingsChanged);
    es.onerror = function () {
      es.close();
      setTimeout(connect, backoff * 1000);
      backoff = nextBackoff(backoff);
    };
  }

  function updateClock() {
    clockEl.textContent = formatClock(new Date());
  }

  function start() {
    applyDom();
    updateClock();
    setInterval(updateClock, 30000);
    Promise.all([loadResume(), loadSettings()]).then(loadPhotos).then(function () {
      if (state.currentId === null && !state.loading) schedule(0);
      connect();
    });
  }

  start();
})(typeof window !== "undefined" ? window : globalThis);
