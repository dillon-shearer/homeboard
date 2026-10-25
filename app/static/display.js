(function (root) {
  "use strict";

  const DEFAULTS = {
    interval_s: 20,
    transition: true,
    fit: "contain",
    shuffle: false,
    captions: false,
    channel: "photos",
    screen_id: null,
    channel_timeout_min: 5,
    clock_size: 5,
    caption_size: 5,
    clock_pos: "bottom-right",
    caption_pos: "bottom-left",
    clock_on_top: true,
    date_size: 3,
    date_pos: "bottom-right",
    date_on_top: false,
    date_show: true,
    cover_size: 3,
    cover_pos: "top-right",
    cover_on_top: true,
    cover_show: true,
    brightness: 100,
  };
  const MAX_BACKOFF_S = 30;
  const SWIPE_PX = 80;
  const FLING_PX = 30;
  const FLING_V = 0.5;
  const TAP_PX = 10;
  const BRIGHTNESS_MIN = 10;
  const BRIGHTNESS_MAX = 100;
  const CHANNELS = ["photos", "screen"];
  const POSITIONS = [
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
  ];

  function validSize(v) {
    return Number.isInteger(v) && v >= 2 && v <= 12;
  }

  function applySettings(prev, incoming) {
    const base = Object.assign({}, DEFAULTS, prev || {});
    const s = incoming || {};
    const out = Object.assign({}, base);
    if (typeof s.interval_s === "number" && s.interval_s > 0) out.interval_s = s.interval_s;
    if ("transition" in s) out.transition = !!s.transition;
    if ("fit" in s) out.fit = s.fit === "cover" ? "cover" : "contain";
    if ("shuffle" in s) out.shuffle = !!s.shuffle;
    if ("captions" in s) out.captions = !!s.captions;
    if (CHANNELS.indexOf(s.channel) !== -1) out.channel = s.channel;
    if (s.screen_id === null || Number.isInteger(s.screen_id)) out.screen_id = s.screen_id;
    if (typeof s.channel_timeout_min === "number" && s.channel_timeout_min >= 0) {
      out.channel_timeout_min = s.channel_timeout_min;
    }
    if (validSize(s.clock_size)) out.clock_size = s.clock_size;
    if (validSize(s.caption_size)) out.caption_size = s.caption_size;
    if (POSITIONS.indexOf(s.clock_pos) !== -1) out.clock_pos = s.clock_pos;
    if (POSITIONS.indexOf(s.caption_pos) !== -1) out.caption_pos = s.caption_pos;
    if ("clock_on_top" in s) out.clock_on_top = !!s.clock_on_top;
    if (validSize(s.date_size)) out.date_size = s.date_size;
    if (POSITIONS.indexOf(s.date_pos) !== -1) out.date_pos = s.date_pos;
    if ("date_on_top" in s) out.date_on_top = !!s.date_on_top;
    if ("date_show" in s) out.date_show = !!s.date_show;
    if (validSize(s.cover_size)) out.cover_size = s.cover_size;
    if (POSITIONS.indexOf(s.cover_pos) !== -1) out.cover_pos = s.cover_pos;
    if ("cover_on_top" in s) out.cover_on_top = !!s.cover_on_top;
    if ("cover_show" in s) out.cover_show = !!s.cover_show;
    if (typeof s.brightness === "number" && isFinite(s.brightness)) out.brightness = clampBrightness(s.brightness);
    return out;
  }

  function clampBrightness(v) {
    return Math.max(BRIGHTNESS_MIN, Math.min(BRIGHTNESS_MAX, Math.round(v)));
  }

  function dimOpacity(v) {
    return (100 - clampBrightness(v)) / 100;
  }

  function overlayLayout(settings) {
    const s = applySettings(null, settings);
    const order = s.clock_on_top ? ["clock", "caption"] : ["caption", "clock"];
    const anchor = s.date_pos !== s.clock_pos && s.date_pos === s.caption_pos ? "caption" : "clock";
    order.splice(order.indexOf(anchor) + (s.date_on_top ? 0 : 1), 0, "date");
    if (s.cover_on_top) order.unshift("cover");
    else order.push("cover");
    return {
      clockCell: s.clock_pos,
      captionCell: s.caption_pos,
      dateCell: s.date_pos,
      coverCell: s.cover_pos,
      order: order,
    };
  }

  function spotifyScreenId(screens) {
    const list = Array.isArray(screens) ? screens : [];
    for (let i = 0; i < list.length; i++) {
      const screen = list[i];
      if (!screen || !Number.isInteger(screen.id) || !Array.isArray(screen.widgets)) continue;
      if (screen.widgets.some(function (w) { return w && w.kind === "spotify"; })) return screen.id;
    }
    return null;
  }

  function coverVisible(now, screens) {
    if (!now || now.linked !== true || now.playing !== true || now.on_board !== true) return false;
    if (typeof now.cover !== "string" || !now.cover) return false;
    return spotifyScreenId(screens) !== null;
  }

  function formatDate(d) {
    return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  }

  function buildStops(screens) {
    const stops = [{ channel: "photos" }];
    (Array.isArray(screens) ? screens : []).forEach(function (screen) {
      if (screen && Number.isInteger(screen.id)) stops.push({ channel: "screen", screen_id: screen.id });
    });
    return stops;
  }

  function stopIndex(stops, settings) {
    const s = settings || {};
    let firstScreen = -1;
    for (let i = 0; i < stops.length; i++) {
      if (stops[i].channel !== s.channel) continue;
      if (s.channel !== "screen" || stops[i].screen_id === s.screen_id) return i;
      if (firstScreen === -1) firstScreen = i;
    }
    if (firstScreen !== -1) return firstScreen;
    return s.channel === "screen" ? -1 : 0;
  }

  function nextStop(stops, settings, step) {
    const n = stops.length;
    const d = step === undefined ? 1 : step;
    let i = stopIndex(stops, settings);
    if (i === -1) i = d > 0 ? n - 1 : n;
    return stops[(((i + d) % n) + n) % n];
  }

  function dragStop(stops, settings, dx) {
    return nextStop(stops, settings, dx < 0 ? 1 : -1);
  }

  function restOffset(channel, width) {
    return channel === "photos" ? width : 0;
  }

  function dragOffset(stops, settings, dx, width) {
    const s = settings || {};
    if (!dx) return restOffset(s.channel, width);
    const d = Math.max(-width, Math.min(width, dx));
    const target = dragStop(stops, s, dx);
    if (s.channel === "photos") {
      if (target.channel === "photos") return null;
      return d < 0 ? width + d : d - width;
    }
    return target.channel === "photos" ? d : null;
  }

  function swipeCommits(dx, dy, v) {
    if (Math.abs(dx) <= Math.abs(dy || 0)) return false;
    if (Math.abs(dx) > SWIPE_PX) return true;
    const speed = v || 0;
    return Math.abs(dx) > FLING_PX && Math.abs(speed) >= FLING_V && speed * dx > 0;
  }

  function panelCommits(dx, dy, v) {
    return dy > 0 && swipeCommits(dy, dx, v);
  }

  function inScroller(el, styleOf) {
    for (; el && el.nodeType === 1; el = el.parentElement) {
      const oy = styleOf(el).overflowY;
      if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight) return true;
    }
    return false;
  }

  function panelOffset(dy, height, open) {
    const d = Math.max(-height, Math.min(height, dy || 0));
    return open ? Math.min(0, d) : Math.max(-height, -height + d);
  }

  function shouldReturn(now, last, timeoutMin, channel) {
    return channel !== "photos" && timeoutMin > 0 && now - last > timeoutMin * 60000;
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

  function focusPosition(photo) {
    const x = photo && typeof photo.focus_x === "number" ? photo.focus_x : 50;
    const y = photo && typeof photo.focus_y === "number" ? photo.focus_y : 50;
    return x + "% " + y + "%";
  }

  const Slideshow = {
    DEFAULTS: DEFAULTS,
    applySettings: applySettings,
    overlayLayout: overlayLayout,
    spotifyScreenId: spotifyScreenId,
    coverVisible: coverVisible,
    POSITIONS: POSITIONS,
    shuffleIds: shuffleIds,
    buildOrder: buildOrder,
    nextIndex: nextIndex,
    mergeList: mergeList,
    orderAround: orderAround,
    resumeOrder: resumeOrder,
    nextBackoff: nextBackoff,
    formatClock: formatClock,
    formatDate: formatDate,
    CHANNELS: CHANNELS,
    buildStops: buildStops,
    nextStop: nextStop,
    SWIPE_PX: SWIPE_PX,
    dragStop: dragStop,
    restOffset: restOffset,
    dragOffset: dragOffset,
    swipeCommits: swipeCommits,
    panelCommits: panelCommits,
    inScroller: inScroller,
    panelOffset: panelOffset,
    shouldReturn: shouldReturn,
    focusPosition: focusPosition,
    clampBrightness: clampBrightness,
    dimOpacity: dimOpacity,
  };

  root.Slideshow = Slideshow;
  if (typeof module !== "undefined" && module.exports) module.exports = Slideshow;
  if (typeof document === "undefined") return;

  const RETRY_MS = 5000;
  const LOAD_TIMEOUT_MS = 15000;
  const CAPTION_FADE_MS = 500;
  const IDLE_CHECK_MS = 15000;
  const SLIDE_MS = 250;
  const VELOCITY_MS = 100;
  const SPLASH_FADE_MS = 500;
  const COVER_POLL_MS = 15000;
  const COVER_END_MS = 1000;
  const PHOTOS_ICON = '<svg class="w95-icon-img" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="5" width="28" height="22" fill="#000"/><rect x="3" y="6" width="26" height="20" fill="#c0c0c0"/><rect x="5" y="8" width="22" height="16" fill="#00ffff"/><polygon points="5,24 13,14 19,21 22,18 27,24" fill="#008000"/><rect x="21" y="10" width="4" height="4" fill="#ffff00"/></svg>';
  const SCREEN_ICON = '<svg class="w95-icon-img" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="4" width="28" height="20" fill="#000"/><rect x="3" y="5" width="26" height="18" fill="#c0c0c0"/><rect x="5" y="7" width="22" height="14" fill="#000080"/><rect x="7" y="9" width="8" height="2" fill="#00ffff"/><rect x="7" y="13" width="12" height="2" fill="#00ffff"/><rect x="24" y="22" width="2" height="1" fill="#00ff00"/><rect x="13" y="24" width="6" height="3" fill="#808080"/><rect x="8" y="27" width="16" height="2" fill="#000"/><rect x="9" y="27" width="14" height="1" fill="#c0c0c0"/></svg>';

  const layerA = document.getElementById("layer-a");
  const layerB = document.getElementById("layer-b");
  const captionEl = document.getElementById("caption");
  const clockEl = document.getElementById("clock");
  const dateEl = document.getElementById("date");
  const coverEl = document.getElementById("cover");
  const channelEl = document.getElementById("channel");
  const overlayEl = document.getElementById("overlay");
  const splashEl = document.getElementById("splash");
  const dimEl = document.getElementById("dim");
  const panelEl = document.getElementById("control-panel");
  const screensEl = document.getElementById("cp-screens");
  const speakerEl = document.getElementById("cp-speaker");
  const speakerStateEl = document.getElementById("cp-speaker-state");
  const sliderEl = document.getElementById("cp-brightness");
  const sliderValueEl = document.getElementById("cp-brightness-value");
  const confirmEl = document.getElementById("cp-confirm");
  const confirmErrorEl = document.getElementById("cp-confirm-error");
  const rebootEl = document.getElementById("cp-restart-yes");

  const state = {
    photos: [],
    byId: {},
    order: [],
    pos: -1,
    settings: applySettings(null, null),
    stops: buildStops([]),
    screens: [],
    now: null,
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
  let lastActivity = Date.now();
  let touchStart = null;
  let lastMove = null;
  let dragging = false;
  let sliding = false;
  let tvDrag = false;
  let shownId = null;
  let coverTouch = null;
  let coverEndTimer = null;
  let panelOpen = false;
  let panelDrag = false;
  let panelSliding = false;
  let sliderDrag = false;
  let speakerOn = false;
  let speakerBusy = false;
  let rebootBusy = false;
  let restarting = false;

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
    state.timer = null;
    if (panelOpen) return;
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
    placeOverlay(state.settings);
    applyChannel();
    if (!sliderDrag) applyBrightness(state.settings.brightness);
    if (sliderEl && !sliderDrag) showSlider(state.settings.brightness);
  }

  function applyBrightness(v) {
    dimEl.style.opacity = String(dimOpacity(v));
  }

  function viewHeight() {
    return root.innerHeight || 0;
  }

  function saveBrightness(v) {
    applyBrightness(v);
    if (v === state.settings.brightness) return;
    state.settings = applySettings(state.settings, { brightness: v });
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brightness: v }),
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
    }).catch(function () {
      loadSettings();
    });
  }

  function placeOverlay(settings) {
    const rootStyle = document.documentElement.style;
    rootStyle.setProperty("--clock-size", settings.clock_size + "vh");
    rootStyle.setProperty("--caption-size", settings.caption_size + "vh");
    rootStyle.setProperty("--date-size", settings.date_size + "vh");
    rootStyle.setProperty("--cover-size", String(settings.cover_size));
    dateEl.hidden = !settings.date_show;
    showCover();
    const layout = overlayLayout(settings);
    const nodes = { clock: clockEl, caption: captionEl, date: dateEl, cover: coverEl };
    const cells = {
      clock: document.getElementById("cell-" + layout.clockCell),
      caption: document.getElementById("cell-" + layout.captionCell),
      date: document.getElementById("cell-" + layout.dateCell),
      cover: document.getElementById("cell-" + layout.coverCell),
    };
    const placed = layout.order.every(function (name, i) {
      if (nodes[name].parentNode !== cells[name]) return false;
      const later = layout.order.slice(i + 1).filter(function (n) { return cells[n] === cells[name]; });
      return !later.length || nodes[name].nextElementSibling === nodes[later[0]];
    });
    if (placed) return;
    layout.order.forEach(function (name) {
      cells[name].appendChild(nodes[name]);
    });
  }

  function showCover() {
    const on = state.settings.cover_show && coverVisible(state.now, state.screens);
    if (on && coverEl.getAttribute("src") !== state.now.cover) coverEl.src = state.now.cover;
    coverEl.hidden = !on;
  }

  function pollCover() {
    if (state.settings.channel !== "photos") return;
    if (!state.settings.cover_show || spotifyScreenId(state.screens) === null) {
      state.now = null;
      showCover();
      return;
    }
    return fetchJson("/api/spotify/now").then(function (now) {
      state.now = now;
      coverAtEnd(now);
    }, function () {
      state.now = null;
    }).then(showCover);
  }

  function coverAtEnd(now) {
    clearTimeout(coverEndTimer);
    coverEndTimer = null;
    if (!now || !now.playing || typeof now.progress_ms !== "number" || !now.duration_ms) return;
    const left = now.duration_ms - now.progress_ms + COVER_END_MS;
    if (left < COVER_POLL_MS) coverEndTimer = setTimeout(pollCover, Math.max(left, COVER_END_MS));
  }

  function openSpotify() {
    const id = spotifyScreenId(state.screens);
    if (id === null || sliding || dragging || state.settings.channel !== "photos") return;
    markActivity();
    const stop = { channel: "screen", screen_id: id };
    showStop(tvScreen(), stop);
    slideChannel(0, function () { setChannel(stop); });
  }

  function onCoverStart(e) {
    const t = e.changedTouches[0];
    coverTouch = t && e.touches.length === 1 ? { x: t.clientX, y: t.clientY } : null;
  }

  function onCoverMove(e) {
    const t = e.changedTouches[0];
    if (!coverTouch || !t) return;
    if (Math.abs(t.clientX - coverTouch.x) > TAP_PX || Math.abs(t.clientY - coverTouch.y) > TAP_PX) coverTouch = null;
  }

  function onCoverEnd(e) {
    const t = e.changedTouches[0];
    const start = coverTouch;
    coverTouch = null;
    if (!start || !t) return;
    if (Math.abs(t.clientX - start.x) > TAP_PX || Math.abs(t.clientY - start.y) > TAP_PX) return;
    openSpotify();
  }

  function applyChannel() {
    const url = "/tv";
    if (channelEl.getAttribute("src") !== url) channelEl.src = url;
    if (dragging || sliding) return;
    channelEl.style.transform = state.settings.channel === "photos" ? "translateX(100vw)" : "translateX(0px)";
  }

  function viewWidth() {
    return root.innerWidth || 0;
  }

  function moveChannel(px) {
    channelEl.style.transform = "translateX(" + px + "px)";
  }

  function slideChannel(px, done) {
    sliding = true;
    channelEl.classList.remove("drag");
    channelEl.classList.add("slide");
    moveChannel(px);
    setTimeout(function () {
      channelEl.classList.remove("slide");
      sliding = false;
      if (done) done();
      applyChannel();
    }, SLIDE_MS);
  }

  function tvScreen() {
    try {
      const w = channelEl.contentWindow;
      const tv = w && w.TvScreen;
      return tv && typeof tv.dragTo === "function" ? tv : null;
    } catch (e) {
      return null;
    }
  }

  function screenTarget(dx) {
    if (!dx || state.settings.channel !== "screen") return null;
    const stop = dragStop(state.stops, state.settings, dx);
    return stop.channel === "screen" ? stop.screen_id : null;
  }

  function slideTv(pending, done) {
    sliding = true;
    Promise.resolve(pending).catch(function () {}).then(function () {
      sliding = false;
      if (done) done();
      applyChannel();
    });
  }

  function setChannel(stop) {
    const body = { channel: stop.channel };
    if (stop.channel === "screen") body.screen_id = stop.screen_id;
    const s = state.settings;
    if (body.channel === s.channel && (body.channel !== "screen" || body.screen_id === s.screen_id)) {
      return;
    }
    state.settings = applySettings(state.settings, body);
    applyChannel();
    if (body.channel === "photos") pollCover();
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
    }).catch(function () {
      loadSettings();
    });
  }

  function markActivity() {
    lastActivity = Date.now();
  }

  function frameLeft(e) {
    const node = e.target;
    const doc = node && (node.ownerDocument || node);
    if (!doc || doc === document || !channelEl.getBoundingClientRect) return 0;
    return channelEl.getBoundingClientRect().left;
  }

  function touchPoint(e) {
    const t = e.changedTouches[0];
    return t ? { x: t.clientX + frameLeft(e), y: t.clientY, t: Date.now() } : null;
  }

  function releaseVelocity(start, p, axis) {
    const k = axis || "x";
    for (let i = 0; i < start.samples.length; i++) {
      const dt = p.t - start.samples[i].t;
      if (dt <= VELOCITY_MS) return dt > 0 ? (p[k] - start.samples[i][k]) / dt : 0;
    }
    return 0;
  }

  function onTouchStart(e) {
    markActivity();
    const p = sliding || panelSliding ? null : touchPoint(e);
    touchStart = p ? { x: p.x, y: p.y, samples: [p], grab: panelGrab(e) } : null;
    lastMove = null;
    shownId = null;
    if (e.target && e.target.addEventListener) {
      e.target.addEventListener("touchmove", onTouchMove, { passive: true });
      e.target.addEventListener("touchend", onTouchEnd, { passive: true, once: true });
    }
  }

  function onTouchMove(e) {
    if (!touchStart || sliding || e === lastMove) return;
    lastMove = e;
    const p = touchPoint(e);
    if (!p) return;
    touchStart.samples.push(p);
    if (touchStart.samples.length > 20) touchStart.samples.shift();
    const dx = p.x - touchStart.x;
    const dy = p.y - touchStart.y;
    if (panelMove(dx, dy)) return;
    const lx = Math.abs(dx) > Math.abs(dy) ? dx : 0;
    const target = screenTarget(lx);
    const tv = tvScreen();
    if (target !== null && tv) {
      dragging = true;
      tvDrag = true;
      tv.dragTo(lx, target);
      return;
    }
    if (tvDrag && tv) tv.dragTo(0, null);
    tvDrag = false;
    const px = dragOffset(state.stops, state.settings, lx, viewWidth());
    if (px === null) return;
    if (lx && state.settings.channel === "photos") showStop(tv, dragStop(state.stops, state.settings, lx));
    dragging = true;
    channelEl.classList.add("drag");
    moveChannel(px);
  }

  function showStop(tv, stop) {
    if (!tv || typeof tv.show !== "function" || stop.channel !== "screen" || stop.screen_id === shownId) return;
    shownId = stop.screen_id;
    tv.show(stop.screen_id);
  }

  function onTouchEnd(e) {
    markActivity();
    if (e.target && e.target.removeEventListener) e.target.removeEventListener("touchmove", onTouchMove);
    const p = touchPoint(e);
    const start = touchStart;
    if (!start || !p) return;
    touchStart = null;
    const dx = p.x - start.x;
    const dy = p.y - start.y;
    if (panelEnd(start, p, dx, dy)) return;
    const wasDragging = dragging;
    const wasTv = tvDrag;
    dragging = false;
    tvDrag = false;
    const width = viewWidth();
    const tv = tvScreen();
    if (!swipeCommits(dx, dy, releaseVelocity(start, p))) {
      if (wasTv && tv) slideTv(tv.cancel());
      else if (wasDragging) slideChannel(restOffset(state.settings.channel, width));
      return;
    }
    const stop = dragStop(state.stops, state.settings, dx);
    if (screenTarget(dx) !== null && tv) {
      tv.dragTo(dx, stop.screen_id);
      slideTv(tv.commit(), function () { setChannel(stop); });
      return;
    }
    if (wasTv && tv) tv.dragTo(0, null);
    const px = dragOffset(state.stops, state.settings, dx < 0 ? -width : width, width);
    if (px === null) {
      channelEl.classList.remove("drag");
      setChannel(stop);
      return;
    }
    if (state.settings.channel === "photos") showStop(tv, stop);
    slideChannel(px, function () { setChannel(stop); });
  }

  function panelGrab(e) {
    if (!panelEl || e.target === sliderEl || !confirmEl.hidden) return false;
    return panelOpen || !inScroller(e.target, function (el) {
      return el.ownerDocument.defaultView.getComputedStyle(el);
    });
  }

  function panelMove(dx, dy) {
    if (!panelEl) return false;
    const steep = Math.abs(dy) > Math.abs(dx);
    if (panelOpen) {
      if (panelDrag || (touchStart.grab && dy < 0 && steep)) {
        panelDrag = true;
        movePanel(panelOffset(dy, viewHeight(), true));
      }
      return true;
    }
    if (!panelDrag) {
      if (!touchStart.grab || dy <= 0 || !steep) return false;
      if (dragging) {
        dragging = false;
        channelEl.classList.remove("drag");
        moveChannel(restOffset(state.settings.channel, viewWidth()));
      }
      if (tvDrag) {
        tvDrag = false;
        const tv = tvScreen();
        if (tv) tv.dragTo(0, null);
      }
      panelDrag = true;
      preparePanel();
    }
    movePanel(panelOffset(dy, viewHeight(), false));
    return true;
  }

  function panelEnd(start, p, dx, dy) {
    const wasPanel = panelDrag;
    panelDrag = false;
    if (!wasPanel) return panelOpen;
    const v = releaseVelocity(start, p, "y");
    if (panelOpen) {
      if (dy < 0 && swipeCommits(dy, dx, v)) closePanel();
      else slidePanel(0);
    } else if (panelCommits(dx, dy, v)) {
      openPanel();
    } else {
      closePanel();
    }
    return true;
  }

  function movePanel(px) {
    panelEl.style.transform = "translateY(" + px + "px)";
  }

  function slidePanel(px, done) {
    panelSliding = true;
    panelEl.classList.add("slide");
    movePanel(px);
    setTimeout(function () {
      panelEl.classList.remove("slide");
      panelSliding = false;
      if (done) done();
    }, SLIDE_MS);
  }

  function preparePanel() {
    if (!panelEl.hidden) return;
    confirmEl.hidden = true;
    renderScreens();
    showSlider(state.settings.brightness);
    loadSpeaker();
    movePanel(-viewHeight());
    panelEl.hidden = false;
  }

  function openPanel() {
    preparePanel();
    panelOpen = true;
    schedule();
    slidePanel(0);
  }

  function closePanel() {
    if (!panelEl || (!panelOpen && panelEl.hidden)) return;
    panelOpen = false;
    panelDrag = false;
    confirmEl.hidden = true;
    if (!state.loading) schedule();
    slidePanel(-viewHeight(), function () {
      if (panelOpen) return;
      panelEl.hidden = true;
    });
  }

  function renderScreens() {
    screensEl.textContent = "";
    addIcon(PHOTOS_ICON, "Photos", { channel: "photos" });
    state.screens.forEach(function (screen) {
      if (screen && Number.isInteger(screen.id)) {
        addIcon(SCREEN_ICON, String(screen.name || ""), { channel: "screen", screen_id: screen.id });
      }
    });
  }

  function addIcon(svg, name, stop) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "w95-icon cp-icon";
    b.innerHTML = svg;
    const label = document.createElement("span");
    label.className = "w95-icon-label";
    label.textContent = name;
    b.appendChild(label);
    b.addEventListener("click", function () { pickStop(stop); });
    li.appendChild(b);
    screensEl.appendChild(li);
  }

  function pickStop(stop) {
    markActivity();
    closePanel();
    if (stop.channel === "photos") {
      if (state.settings.channel !== "photos") setChannel(stop);
      return;
    }
    shownId = null;
    showStop(tvScreen(), stop);
    slideChannel(0, function () { setChannel(stop); });
  }

  function showSlider(v) {
    sliderEl.value = String(v);
    sliderValueEl.textContent = v + "%";
  }

  function onSliderInput() {
    markActivity();
    sliderDrag = true;
    const v = clampBrightness(Number(sliderEl.value));
    sliderValueEl.textContent = v + "%";
    applyBrightness(v);
  }

  function onSliderChange() {
    markActivity();
    sliderDrag = false;
    saveBrightness(clampBrightness(Number(sliderEl.value)));
  }

  function renderSpeaker(text, on) {
    speakerOn = on;
    speakerEl.setAttribute("aria-checked", on ? "true" : "false");
    speakerStateEl.textContent = text;
  }

  function speakerError(err) {
    renderSpeaker(err && err.message === "503" ? "Helper not running" : "Speaker status unknown", false);
  }

  function loadSpeaker() {
    return fetchJson("/api/sound/status").then(function (s) {
      const on = !!(s && s.speaker && s.speaker.connected);
      renderSpeaker(on ? "Connected" : "Off", on);
    }, speakerError);
  }

  function postJson(url) {
    return fetch(url, { method: "POST" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
    });
  }

  function toggleSpeaker() {
    markActivity();
    if (speakerBusy) return;
    speakerBusy = true;
    speakerEl.disabled = true;
    const on = speakerOn;
    renderSpeaker(on ? "Disconnecting..." : "Connecting...", !on);
    postJson(on ? "/api/sound/disconnect" : "/api/sound/connect").then(loadSpeaker, function (err) {
      if (err.message === "503") speakerError(err);
      else return loadSpeaker();
    }).then(function () {
      speakerBusy = false;
      speakerEl.disabled = false;
    });
  }

  function askRestart() {
    markActivity();
    confirmErrorEl.hidden = true;
    confirmEl.hidden = false;
  }

  function cancelRestart() {
    markActivity();
    confirmEl.hidden = true;
  }

  function restartBoard() {
    markActivity();
    if (rebootBusy) return;
    rebootBusy = true;
    rebootEl.disabled = true;
    postJson("/api/sound/reboot").then(showRestarting, function (err) {
      confirmErrorEl.textContent = err.message === "503"
        ? "Helper not running. The board was not restarted."
        : "Could not restart the board. Try again.";
      confirmErrorEl.hidden = false;
    }).then(function () {
      rebootBusy = false;
      rebootEl.disabled = false;
    });
  }

  function showRestarting() {
    restarting = true;
    confirmEl.hidden = true;
    const label = splashEl.querySelector("#label");
    if (label) label.textContent = "Home Board is restarting...";
    splashEl.classList.remove("fade");
    splashEl.hidden = false;
  }

  function hookPanel() {
    document.getElementById("cp-close").addEventListener("click", function () {
      markActivity();
      closePanel();
    });
    speakerEl.addEventListener("click", toggleSpeaker);
    sliderEl.addEventListener("input", onSliderInput);
    sliderEl.addEventListener("change", onSliderChange);
    document.getElementById("cp-restart").addEventListener("click", askRestart);
    document.getElementById("cp-restart-no").addEventListener("click", cancelRestart);
    rebootEl.addEventListener("click", restartBoard);
  }

  function hookFrame() {
    let doc = null;
    try {
      doc = channelEl.contentDocument;
    } catch (e) {
      doc = null;
    }
    if (!doc) return;
    doc.addEventListener("touchstart", onTouchStart, { passive: true });
    doc.addEventListener("touchmove", onTouchMove, { passive: true });
    doc.addEventListener("touchend", onTouchEnd, { passive: true });
  }

  function checkIdle() {
    const s = state.settings;
    if (panelOpen && !panelDrag && !restarting && shouldReturn(Date.now(), lastActivity, s.channel_timeout_min, "panel")) {
      closePanel();
    }
    if (dragging || sliding || !shouldReturn(Date.now(), lastActivity, s.channel_timeout_min, s.channel)) return;
    slideChannel(viewWidth(), function () { setChannel({ channel: "photos" }); });
  }

  function setSettings(data) {
    const prev = state.settings;
    state.settings = applySettings(prev, data);
    applyDom();
    if (prev.channel !== "photos" && state.settings.channel === "photos") pollCover();
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

  function loadScreens() {
    return fetchJson("/api/screens").then(function (list) {
      state.stops = buildStops(list);
      state.screens = Array.isArray(list) ? list : [];
      showCover();
      if (panelOpen) renderScreens();
    }, function () {});
  }

  function setPhotos(list) {
    const hadCurrent = state.currentId !== null;
    const newIds = list.map(function (p) { return p.id; });
    const m = mergeList(state.order, newIds, state.currentId);
    state.photos = list;
    state.byId = {};
    list.forEach(function (p) { state.byId[p.id] = p; });
    [layerA, layerB].forEach(function (l) {
      const p = state.byId[l.dataset.id];
      if (p) l.style.objectPosition = focusPosition(p);
    });
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

  function hideSplash(delay) {
    if (restarting || !splashEl || splashEl.hidden || splashEl.classList.contains("fade")) return;
    setTimeout(function () {
      splashEl.classList.add("fade");
      setTimeout(function () { splashEl.hidden = true; }, SPLASH_FADE_MS);
    }, delay);
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
      hideSplash(0);
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
      document.body.insertBefore(node, overlayEl);
      if (!node.hidden) hideSplash(0);
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
    captionTimer = null;
    captionEl.classList.remove("show");
  }

  function showCaption(photo) {
    const text = state.settings.captions && photo.caption ? photo.caption : "";
    const shown = captionEl.classList.contains("show");
    if (shown && !captionTimer && captionEl.textContent === text) return;
    hideCaption();
    if (!text) return;
    if (!shown) {
      captionEl.textContent = text;
      captionEl.classList.add("show");
      return;
    }
    captionTimer = setTimeout(function () {
      captionTimer = null;
      captionEl.textContent = text;
      captionEl.classList.add("show");
    }, CAPTION_FADE_MS);
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
    layer.style.objectPosition = focusPosition(photo);
    layer.src = photo.url;
  }

  function swap(layer, photo, token) {
    state.back = state.front;
    state.front = layer;
    layer.style.objectPosition = focusPosition(photo);
    layer.classList.add("front");
    state.back.classList.remove("front");
    state.currentId = photo.id;
    state.shownAt = Date.now();
    state.resumeId = null;
    reportCurrent(photo.id);
    showCaption(photo);
    hideSplash(transitionMs());
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
      loadScreens();
      onSettingsChanged();
      onPhotosChanged();
    });
    es.addEventListener("photos_changed", function () {
      markActivity();
      onPhotosChanged();
    });
    es.addEventListener("settings_changed", function () {
      markActivity();
      onSettingsChanged();
    });
    es.addEventListener("lists_changed", function () {
      markActivity();
      loadScreens();
    });
    es.addEventListener("events_changed", markActivity);
    es.addEventListener("spotify_changed", pollCover);
    es.onerror = function () {
      es.close();
      setTimeout(connect, backoff * 1000);
      backoff = nextBackoff(backoff);
    };
  }

  function updateClock() {
    const now = new Date();
    clockEl.textContent = formatClock(now);
    dateEl.textContent = formatDate(now);
    setTimeout(updateClock, 60000 - now.getSeconds() * 1000 - now.getMilliseconds() + 20);
  }

  function start() {
    applyDom();
    updateClock();
    document.addEventListener("touchstart", onTouchStart, { passive: true });
    document.addEventListener("touchmove", onTouchMove, { passive: true });
    document.addEventListener("touchend", onTouchEnd, { passive: true });
    channelEl.onload = hookFrame;
    if (panelEl) hookPanel();
    coverEl.addEventListener("touchstart", onCoverStart, { passive: true });
    coverEl.addEventListener("touchmove", onCoverMove, { passive: true });
    coverEl.addEventListener("touchend", onCoverEnd, { passive: true });
    coverEl.addEventListener("touchcancel", function () { coverTouch = null; }, { passive: true });
    setInterval(pollCover, COVER_POLL_MS);
    setInterval(checkIdle, IDLE_CHECK_MS);
    Promise.all([loadResume(), loadSettings(), loadScreens()]).then(function () {
      pollCover();
      return loadPhotos();
    }).then(function () {
      if (state.currentId === null && !state.loading) schedule(0);
      connect();
    });
  }

  start();
})(typeof window !== "undefined" ? window : globalThis);
