(function () {
  "use strict";

  var POLL_MS = 5000;
  var END_MS = 1000;
  var AFTER_TAP_MS = 1000;
  var TICK_MS = 1000;
  var EXTRA_EVERY = 3;
  var NOTE_MS = 3000;
  var SEND_MS = 250;
  var HOLD_MS = 2500;
  var STEP = 5;
  var CHAIN_MS = 1000;
  var MAX_BACKOFF_S = 30;
  var MAX_POLL_MS = 60000;
  var IDLE_TEXT = "Nothing playing";
  var SPOTIFY_DOWN_TEXT = "Could not reach Spotify";
  var SPEAKER_DOWN_TEXT = "Could not reach the speaker";

  var now = null;
  var at = 0;
  var linked = null;
  var seq = 0;
  var polls = 0;
  var timer = null;
  var noteTimer = null;
  var sendTimer = null;
  var touchedAt = 0;
  var vol = null;
  var sound = null;
  var busy = false;
  var ui = null;
  var backoff = 1;
  var fails = 0;
  var queue = Promise.resolve();

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (out) {
        if (!r.ok) {
          var err = new Error("HTTP " + r.status);
          err.status = r.status;
          throw err;
        }
        return out;
      });
    });
  }

  function formatMs(ms) {
    var s = Math.max(0, Math.floor((ms || 0) / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function progressAt(n, sinceMs) {
    if (!n || typeof n.progress_ms !== "number") return 0;
    var ms = n.progress_ms + (n.playing && !n.stale ? Math.max(0, sinceMs || 0) : 0);
    return n.duration_ms ? Math.min(ms, n.duration_ms) : ms;
  }

  function nextPollMs(n) {
    if (!n || !n.playing || typeof n.progress_ms !== "number" || !n.duration_ms) return POLL_MS;
    var left = n.duration_ms - n.progress_ms + END_MS;
    return Math.max(END_MS, Math.min(POLL_MS, left));
  }

  function backoffMs(n) {
    if (!fails) return nextPollMs(n);
    return Math.min(MAX_POLL_MS, POLL_MS * Math.pow(2, fails - 1));
  }

  function staleText(n) {
    if (!n || !n.stale) return "";
    var why = n.reason === "rate limited" ? "Could not reach Spotify (too many requests)." : "Could not reach Spotify.";
    return n.track ? why + " Showing the last known song." : why;
  }

  function deviceText(n) {
    if (!n || !n.device) return "";
    return n.on_board ? "Playing on Home Board" : "Playing on " + n.device;
  }

  function volumeStep(volume, delta) {
    if (typeof volume !== "number") return null;
    return Math.max(0, Math.min(100, volume + delta));
  }

  function live() {
    return !!(now && now.track);
  }

  function note(text) {
    var p = $("player-note");
    p.textContent = text || "";
    p.hidden = !text;
    clearTimeout(noteTimer);
    if (text) noteTimer = setTimeout(function () { note(""); }, NOTE_MS);
  }

  function spButton(action, label) {
    var b = el("button", "w95-btn player-btn", label);
    b.type = "button";
    b.setAttribute("data-sp", action);
    return b;
  }

  function buildPlayer() {
    var box = $("player");
    box.textContent = "";
    ui = {};
    ui.cover = box.appendChild(el("div", "player-cover none"));
    ui.img = el("img");
    ui.img.alt = "";
    ui.track = box.appendChild(el("div", "player-track", IDLE_TEXT));
    ui.artist = box.appendChild(el("div", "player-artist"));
    ui.device = box.appendChild(el("div", "player-device"));
    ui.status = box.appendChild(el("div", "player-status"));
    ui.progress = box.appendChild(el("div", "player-progress"));
    ui.elapsed = ui.progress.appendChild(el("span", "player-time", "0:00"));
    ui.bar = ui.progress.appendChild(el("span", "player-bar")).appendChild(el("i"));
    ui.total = ui.progress.appendChild(el("span", "player-time", "0:00"));
    ui.transport = box.appendChild(el("div", "player-transport"));
    ui.transport.appendChild(spButton("previous", "Previous"));
    ui.toggle = ui.transport.appendChild(spButton("toggle", "Play"));
    ui.transport.appendChild(spButton("next", "Next"));
    ui.here = box.appendChild(el("div", "player-transport")).appendChild(spButton("here", "Play here"));
    var row = box.appendChild(el("div", "volume-row player-volume"));
    ui.down = row.appendChild(spButton("vol-down", "-"));
    ui.down.className = "w95-btn volume-step";
    ui.down.setAttribute("aria-label", "Volume down");
    ui.slider = row.appendChild(el("input", "volume-slider"));
    ui.slider.type = "range";
    ui.slider.min = "0";
    ui.slider.max = "100";
    ui.slider.step = "1";
    ui.slider.setAttribute("aria-label", "Volume");
    ui.up = row.appendChild(spButton("vol-up", "+"));
    ui.up.className = "w95-btn volume-step";
    ui.up.setAttribute("aria-label", "Volume up");
    ui.value = row.appendChild(el("span", "volume-value", "--%"));
    ui.slider.addEventListener("input", function () {
      touchedAt = Date.now();
      ui.value.textContent = ui.slider.value + "%";
      clearTimeout(sendTimer);
      sendTimer = setTimeout(sendSlider, SEND_MS);
    });
    ui.slider.addEventListener("change", sendSlider);
  }

  function showVolume(v) {
    ui.slider.value = String(v);
    ui.value.textContent = v + "%";
  }

  function render() {
    if (!ui) return;
    var on = live();
    var url = on && typeof now.cover === "string" && now.cover ? now.cover : null;
    if (url) {
      if (ui.img.getAttribute("src") !== url) ui.img.src = url;
      if (!ui.img.parentNode) ui.cover.appendChild(ui.img);
      ui.cover.classList.remove("none");
    } else {
      if (ui.img.parentNode) ui.cover.removeChild(ui.img);
      ui.cover.classList.add("none");
    }
    ui.track.textContent = on ? now.track : IDLE_TEXT;
    ui.artist.textContent = on && now.artist ? now.artist : "";
    ui.artist.hidden = !(on && now.artist);
    ui.device.textContent = deviceText(now);
    ui.device.hidden = !ui.device.textContent;
    ui.status.textContent = staleText(now);
    ui.status.hidden = !ui.status.textContent;
    ui.progress.hidden = !on;
    ui.transport.hidden = !on;
    var label = on && now.playing ? "Pause" : "Play";
    if (ui.toggle.textContent !== label) ui.toggle.textContent = label;
    ui.here.parentNode.hidden = !!(now && now.on_board);
    var known = !!now && typeof now.volume === "number";
    ui.slider.disabled = ui.down.disabled = ui.up.disabled = !known;
    if (!known) ui.value.textContent = "--%";
    else if (Date.now() - touchedAt > HOLD_MS) showVolume(now.volume);
    tick();
  }

  function tick() {
    if (!ui || !live()) return;
    var ms = progressAt(now, Date.now() - at);
    ui.elapsed.textContent = formatMs(ms);
    ui.total.textContent = formatMs(now.duration_ms);
    ui.bar.style.width = (now.duration_ms ? Math.min(100, ms / now.duration_ms * 100) : 0) + "%";
  }

  function setLinked(on) {
    if (on === linked) return;
    linked = on;
    $("not-linked").hidden = on;
    $("speaker-group").hidden = !on;
    $("playlists-group").hidden = !on;
    $("spotify-state").hidden = true;
    if (on) {
      buildPlayer();
      loadExtras();
    } else {
      ui = null;
      $("player").textContent = "";
      $("board-playlists").textContent = "";
      note("");
    }
  }

  function apply(n) {
    now = n;
    at = Date.now();
    setLinked(n.linked !== false);
    render();
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = null;
    if (document.visibilityState !== "hidden") timer = setTimeout(poll, ms);
  }

  function poll() {
    var mine = ++seq;
    clearTimeout(timer);
    timer = null;
    if (document.visibilityState === "hidden") return;
    if (linked && ++polls % EXTRA_EVERY === 0) loadExtras();
    getJSON("/api/spotify/now").then(function (n) {
      if (mine !== seq) return;
      fails = n.stale ? fails + 1 : 0;
      apply(n);
    }, function () {
      if (mine !== seq) return;
      fails++;
      if (linked === null) {
        $("spotify-state").textContent = "Spotify not reachable";
        return;
      }
      if (!now || now.linked === false) return;
      apply(Object.assign({}, now, { progress_ms: progressAt(now, Date.now() - at), stale: true, reason: "unreachable" }));
    }).then(function () {
      if (mine === seq) schedule(backoffMs(now));
    });
  }

  function call(method, url, body) {
    seq++;
    clearTimeout(timer);
    var opts = { method: method };
    if (body) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    queue = queue.then(function () {
      return fetch(url, opts).then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (out) {
            var said = (r.status === 409 || r.status === 429) && typeof out.detail === "string";
            note(said ? out.detail : SPOTIFY_DOWN_TEXT);
          });
        }
      }, function () {
        note(SPOTIFY_DOWN_TEXT);
      });
    }).then(function () {
      schedule(AFTER_TAP_MS);
    });
    return queue;
  }

  function sendSlider() {
    clearTimeout(sendTimer);
    sendTimer = null;
    if (!ui || ui.slider.disabled) return;
    touchedAt = Date.now();
    var v = Number(ui.slider.value);
    if (now) now.volume = v;
    call("PUT", "/api/spotify/volume", { percent: v }).then(function () {
      touchedAt = Date.now();
    });
  }

  function freshVolume() {
    seq++;
    clearTimeout(timer);
    return getJSON("/api/spotify/now?fresh=1").then(function (n) {
      return n.volume;
    }, function () {
      note(SPOTIFY_DOWN_TEXT);
      return null;
    });
  }

  function volumeTap(delta) {
    touchedAt = Date.now();
    var chained = vol && Date.now() - vol.at < CHAIN_MS;
    var sent = (chained ? vol.sent : freshVolume()).then(function (volume) {
      var next = volumeStep(volume, delta);
      if (next === null) {
        schedule(AFTER_TAP_MS);
        return null;
      }
      touchedAt = Date.now();
      if (now) now.volume = next;
      if (ui) showVolume(next);
      return call("PUT", "/api/spotify/volume", { percent: next }).then(function () {
        return next;
      });
    });
    vol = { sent: sent, at: Date.now() };
    var guess = volumeStep(now.volume, delta);
    if (guess !== null) showVolume(guess);
  }

  function tap(btn) {
    var action = btn.getAttribute("data-sp");
    if (!now || btn.disabled) return;
    if (action === "toggle") {
      var playing = !!now.playing;
      now.progress_ms = progressAt(now, Date.now() - at);
      now.playing = !playing;
      at = Date.now();
      render();
      call("POST", playing ? "/api/spotify/pause" : "/api/spotify/play");
    } else if (action === "here") {
      call("POST", "/api/spotify/play-here");
    } else if (action === "previous" || action === "next") {
      call("POST", "/api/spotify/" + action);
    } else if (action === "vol-down" || action === "vol-up") {
      if (typeof now.volume !== "number") return;
      volumeTap(action === "vol-up" ? STEP : -STEP);
    }
  }

  function renderPlaylists(rows) {
    var ul = $("board-playlists");
    ul.textContent = "";
    (rows || []).forEach(function (p) {
      var li = el("li");
      var b = el("button", "w95-btn player-tile");
      b.type = "button";
      b.setAttribute("data-playlist", p.id);
      var thumb = b.appendChild(el("span", "player-thumb" + (p.cover ? "" : " none")));
      if (p.cover) {
        var img = thumb.appendChild(el("img"));
        img.alt = "";
        img.src = p.cover;
      }
      b.appendChild(el("span", "player-name", p.name || "Playlist"));
      li.appendChild(b);
      ul.appendChild(li);
    });
    $("playlists-none").hidden = !!(rows && rows.length);
  }

  function renderSpeaker(s) {
    sound = s;
    var sp = (s && s.speaker) || {};
    $("speaker-state").textContent = sp.connected ? "Connected to " + (sp.name || "the speaker") : "Not connected";
    $("speaker-toggle").textContent = sp.connected ? "Disconnect" : "Connect";
    $("speaker-toggle").disabled = busy;
  }

  function speakerError(err) {
    $("speaker-state").textContent = err.status === 503 ? "Helper not running" : "Speaker status unknown";
    $("speaker-toggle").disabled = busy;
  }

  function loadSpeaker() {
    return getJSON("/api/sound/status").then(function (s) {
      if (!busy) renderSpeaker(s);
    }, function (err) {
      if (!busy) speakerError(err);
    });
  }

  function loadShown() {
    return getJSON("/api/spotify/shown").then(function (rows) {
      if (linked) renderPlaylists(rows);
    }).catch(function () {});
  }

  function loadExtras() {
    loadSpeaker();
    loadShown();
  }

  function toggleSpeaker() {
    if (busy) return;
    var on = !!(sound && sound.speaker && sound.speaker.connected);
    busy = true;
    $("speaker-toggle").disabled = true;
    $("speaker-state").textContent = on ? "Disconnecting..." : "Connecting...";
    fetch(on ? "/api/sound/disconnect" : "/api/sound/connect", { method: "POST" }).then(function (r) {
      if (!r.ok && r.status !== 503) note(SPEAKER_DOWN_TEXT);
    }, function () {
      note(SPEAKER_DOWN_TEXT);
    }).then(function () {
      busy = false;
      return loadSpeaker();
    });
  }

  function connectEvents() {
    if (typeof EventSource === "undefined") return;
    var es;
    try {
      es = new EventSource("/api/events");
    } catch (e) {
      retryEvents();
      return;
    }
    es.onopen = function () { backoff = 1; };
    es.addEventListener("hello", poll);
    es.addEventListener("spotify_changed", poll);
    es.onerror = function () {
      es.close();
      retryEvents();
    };
  }

  function retryEvents() {
    setTimeout(connectEvents, backoff * 1000);
    backoff = Math.min(backoff * 2, MAX_BACKOFF_S);
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-sp], [data-playlist]") : null;
    if (!b) return;
    if (b.hasAttribute("data-playlist")) {
      call("POST", "/api/spotify/play-playlist", { id: b.getAttribute("data-playlist") });
    } else {
      tap(b);
    }
  });
  $("speaker-toggle").addEventListener("click", toggleSpeaker);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    poll();
    if (linked) loadExtras();
  });
  setInterval(tick, TICK_MS);

  connectEvents();
  loadSpeaker();
  poll();
})();
