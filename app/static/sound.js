(function () {
  "use strict";

  var NOW_MS = 5000;
  var STEP_DEBOUNCE_MS = 400;
  var IDLE_MIN = 1;
  var IDLE_MAX = 120;
  var EXPIRY_WARN_MS = 14 * 24 * 60 * 60 * 1000;
  var NO_CREDS = "Spotify is not set up on the Pi yet. See PI_SETUP.md, Spotify.";

  var linked = null;
  var shownIds = {};
  var playlists = [];
  var idle = null;
  var busy = false;

  function $(id) {
    return document.getElementById(id);
  }

  function send(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body === undefined ? {} : body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (out) {
        if (!r.ok) {
          var err = new Error(typeof out.detail === "string" ? out.detail : "Could not save");
          err.status = r.status;
          throw err;
        }
        return out;
      });
    });
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

  function showError(msg) {
    var el = $("settings-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function renderSpotify(s) {
    var was = linked;
    if (s.linked) {
      linked = true;
      $("spotify-state").textContent = s.display_name ? "Linked as " + s.display_name : "Linked to Spotify";
    } else if (s.reason === "no credentials") {
      linked = false;
      $("spotify-state").textContent = NO_CREDS;
    } else {
      linked = false;
      $("spotify-state").textContent = "Not linked";
    }
    $("spotify-login").hidden = s.linked || s.reason === "no credentials";
    $("spotify-unlink").hidden = !s.linked;
    var track = $("spotify-track");
    if (s.linked && s.playing && s.track) {
      track.textContent = "Playing: " + s.track + (s.artist ? " - " + s.artist : "");
      track.hidden = false;
    } else {
      track.textContent = "";
      track.hidden = true;
    }
    if (was !== linked) loadPlaylists();
  }

  function loadNow() {
    return getJSON("/api/spotify/now").then(renderSpotify).catch(function () {
      if (linked !== null) return;
      $("spotify-state").textContent = "Spotify not reachable";
      $("playlists-note").textContent = "Could not load the playlists. Try again later.";
    });
  }

  function renderSpeaker(s) {
    var sp = (s && s.speaker) || {};
    $("speaker-state").textContent = sp.connected ? "Connected to " + (sp.name || "the speaker") : "Not connected";
    var warn = $("soloist-warning");
    var ends = s && s.soloist_expires ? new Date(s.soloist_expires + "T00:00:00").getTime() : NaN;
    if (ends - Date.now() < EXPIRY_WARN_MS) {
      warn.textContent = "Home Board stops playing Spotify on " + s.soloist_expires +
        ". To renew it, on the Pi run: sudo bash /opt/homeboard/deploy/install.sh --update-soloist";
      warn.hidden = false;
    } else {
      warn.textContent = "";
      warn.hidden = true;
    }
  }

  function speakerError(err) {
    $("speaker-state").textContent = err.status === 503 ? "Helper not running" : "Speaker status unknown";
  }

  function loadSpeaker() {
    return getJSON("/api/sound/status").then(renderSpeaker).catch(speakerError);
  }

  function speakerAction(path, label) {
    if (busy) return;
    busy = true;
    $("speaker-connect").disabled = true;
    $("speaker-disconnect").disabled = true;
    $("speaker-state").textContent = label;
    send("POST", path).catch(function (err) {
      if (err.status !== 503) showError("Could not reach the speaker. Try again.");
    }).then(loadSpeaker).then(function () {
      busy = false;
      $("speaker-connect").disabled = false;
      $("speaker-disconnect").disabled = false;
    });
  }

  function renderIdle(v) {
    idle = v;
    if (document.activeElement !== $("idle-minutes")) $("idle-minutes").value = v;
  }

  function loadSettings() {
    return getJSON("/api/settings").then(function (s) {
      renderIdle(s.speaker_idle_minutes);
    }).catch(function () {});
  }

  function validIdle(v) {
    return /^\d+$/.test(String(v).trim()) && Number(v) >= IDLE_MIN && Number(v) <= IDLE_MAX;
  }

  function putIdle(v) {
    if (!validIdle(v) || Number(v) === idle) return;
    send("PUT", "/api/settings", { speaker_idle_minutes: Number(v) }).then(function (s) {
      showError("");
      renderIdle(s.speaker_idle_minutes);
    }).catch(function (err) {
      showError(err.message + ". Try again.");
      loadSettings();
    });
  }

  function idleStepper() {
    var input = $("idle-minutes");
    var timer = null;

    function step(delta) {
      var base = validIdle(input.value) ? Number(input.value) : idle || IDLE_MIN;
      var v = Math.min(IDLE_MAX, Math.max(IDLE_MIN, base + delta));
      input.value = v;
      showError("");
      clearTimeout(timer);
      timer = setTimeout(function () { putIdle(v); }, STEP_DEBOUNCE_MS);
    }

    holdStep($("idle-minutes-minus"), function () { step(-1); });
    holdStep($("idle-minutes-plus"), function () { step(1); });
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var raw = input.value;
      if (raw === "") return;
      if (!validIdle(raw)) {
        showError("Pick from " + IDLE_MIN + " to " + IDLE_MAX + " minutes.");
        return;
      }
      showError("");
      timer = setTimeout(function () { putIdle(raw); }, STEP_DEBOUNCE_MS);
    });
    input.addEventListener("change", function () {
      if (!validIdle(input.value) && idle !== null) {
        input.value = idle;
        showError("");
      }
    });
  }

  function holdStep(btn, step) {
    var delay = null;
    var repeat = null;
    function stop() {
      clearTimeout(delay);
      clearInterval(repeat);
      delay = repeat = null;
    }
    btn.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      stop();
      step();
      delay = setTimeout(function () {
        repeat = setInterval(step, 80);
      }, 400);
    });
    btn.addEventListener("pointerup", stop);
    btn.addEventListener("pointerleave", stop);
    btn.addEventListener("pointercancel", stop);
    btn.addEventListener("click", function (e) {
      if (e.detail === 0) step();
    });
  }

  function renderCount() {
    var n = playlists.filter(function (p) { return shownIds[p.id]; }).length;
    $("shown-count").textContent = "Shown on the board: " + n;
    $("shown-count").hidden = false;
  }

  function saveShown() {
    var body = [];
    var boxes = $("playlists").querySelectorAll("input[type=checkbox]");
    for (var i = 0; i < boxes.length; i++) {
      if (!boxes[i].checked) continue;
      var p = playlists[Number(boxes[i].getAttribute("data-index"))];
      body.push({ id: p.id, name: p.name || null, cover: p.cover || null });
    }
    shownIds = {};
    body.forEach(function (p) { shownIds[p.id] = true; });
    renderCount();
    send("PUT", "/api/spotify/shown", body).then(function () {
      showError("");
    }).catch(function (err) {
      showError(err.message + ". Try again.");
    });
  }

  function renderPlaylists() {
    var ul = $("playlists");
    ul.textContent = "";
    playlists.forEach(function (p, i) {
      var li = document.createElement("li");
      var label = document.createElement("label");
      label.className = "w95-check";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.setAttribute("data-index", String(i));
      box.setAttribute("data-id", p.id);
      box.checked = !!shownIds[p.id];
      box.addEventListener("change", saveShown);
      var span = document.createElement("span");
      span.textContent = p.name || "Untitled playlist";
      label.appendChild(box);
      label.appendChild(span);
      li.appendChild(label);
      ul.appendChild(li);
    });
    $("playlists-note").textContent = playlists.length ? "Tick the playlists to show on the board." : "No playlists in this Spotify account.";
    renderCount();
  }

  function notLinkedPlaylists() {
    playlists = [];
    $("playlists").textContent = "";
    $("playlists-note").textContent = "Sign in to Spotify first";
    $("shown-count").hidden = true;
  }

  function loadPlaylists() {
    if (!linked) {
      notLinkedPlaylists();
      return Promise.resolve();
    }
    $("playlists-note").textContent = "Loading...";
    return getJSON("/api/spotify/shown").then(function (rows) {
      shownIds = {};
      (rows || []).forEach(function (r) { shownIds[r.id] = true; });
      return getJSON("/api/spotify/playlists");
    }).then(function (out) {
      if (!out.linked) {
        notLinkedPlaylists();
        return;
      }
      playlists = out.playlists || [];
      renderPlaylists();
    }).catch(function () {
      $("playlists-note").textContent = "Could not load the playlists. Try again later.";
    });
  }

  function openUnlink() {
    $("unlink-dialog").hidden = false;
    $("unlink-confirm").focus();
  }

  function closeUnlink() {
    $("unlink-dialog").hidden = true;
    $("spotify-unlink").focus();
  }

  function unlink() {
    $("unlink-dialog").hidden = true;
    send("POST", "/api/spotify/unlink").then(function (s) {
      showError("");
      renderSpotify(s);
    }).catch(function (err) {
      showError(err.message + ". Try again.");
    });
  }

  $("spotify-unlink").addEventListener("click", openUnlink);
  $("unlink-confirm").addEventListener("click", unlink);
  $("unlink-cancel").addEventListener("click", closeUnlink);
  $("unlink-close").addEventListener("click", closeUnlink);
  $("unlink-backdrop").addEventListener("click", closeUnlink);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("unlink-dialog").hidden) closeUnlink();
  });
  $("speaker-connect").addEventListener("click", function () { speakerAction("/api/sound/connect", "Connecting..."); });
  $("speaker-disconnect").addEventListener("click", function () { speakerAction("/api/sound/disconnect", "Disconnecting..."); });
  idleStepper();

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    loadNow();
    loadSpeaker();
  });
  setInterval(function () {
    if (document.visibilityState === "visible") loadNow();
  }, NOW_MS);

  loadSettings();
  loadSpeaker();
  loadNow();
})();
