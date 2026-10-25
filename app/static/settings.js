(function () {
  "use strict";

  var STATUS_MS = 10000;
  var MAX_BACKOFF_S = 30;
  var STEP_DEBOUNCE_MS = 400;
  var BRIGHTNESS_MIN = 10;
  var BRIGHTNESS_MAX = 100;
  var RADIO_KEYS = ["fit", "clock_pos", "caption_pos", "clock_on_top", "date_pos", "date_on_top", "cover_pos", "cover_on_top"];

  var current = {};
  var sliding = false;
  var rebootBusy = false;

  function $(id) {
    return document.getElementById(id);
  }

  function send(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function showSettingsError(msg) {
    var el = $("settings-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function renderWeather(s) {
    var input = $("weather-location");
    if (document.activeElement !== input) input.value = s.weather_location || "";
    var note = $("weather-name");
    if (!s.weather_location) note.textContent = "";
    else note.textContent = s.weather_name ? "Showing weather for " + s.weather_name : "Looking up...";
  }

  function clampBrightness(v) {
    v = Math.round(Number(v));
    if (!isFinite(v)) v = BRIGHTNESS_MAX;
    return Math.max(BRIGHTNESS_MIN, Math.min(BRIGHTNESS_MAX, v));
  }

  function showBrightness(v) {
    $("brightness").value = String(v);
    $("brightness-value").textContent = v + "%";
  }

  function renderSettings(s) {
    current = s;
    $("interval").value = s.interval_s;
    $("channel-timeout").value = String(s.channel_timeout_min);
    renderWeather(s);
    $("clock-size").value = s.clock_size;
    $("caption-size").value = s.caption_size;
    $("date-size").value = s.date_size;
    $("cover-size").value = s.cover_size;
    RADIO_KEYS.forEach(function (key) {
      var radios = document.querySelectorAll('input[name="' + key + '"]');
      for (var i = 0; i < radios.length; i++) radios[i].checked = radios[i].value === String(s[key]);
    });
    $("stack-row").hidden = s.clock_pos !== s.caption_pos;
    $("date-stack-row").hidden = s.date_pos !== s.clock_pos && s.date_pos !== s.caption_pos;
    $("cover-stack-row").hidden = [s.clock_pos, s.caption_pos, s.date_pos].indexOf(s.cover_pos) === -1;
    var checks = document.querySelectorAll(".checks input[data-key]");
    for (var n = 0; n < checks.length; n++) checks[n].checked = !!s[checks[n].getAttribute("data-key")];
    if (!sliding && typeof s.brightness === "number") showBrightness(clampBrightness(s.brightness));
  }

  function loadSettings() {
    return getJSON("/api/settings").then(renderSettings);
  }

  function putSettings(patch) {
    return send("PUT", "/api/settings", patch).then(function (s) {
      showSettingsError("");
      renderSettings(s);
      return s;
    }).catch(function (err) {
      showSettingsError(err.message + ". Try again.");
      loadSettings().catch(function () {});
      return null;
    });
  }

  function saveWeather() {
    var input = $("weather-location");
    var text = input.value.trim();
    if (!text || text === current.weather_location) {
      input.value = current.weather_location || "";
      return;
    }
    putSettings({ weather_location: text }).then(function (s) {
      if (!s) return;
      return getJSON("/api/weather").catch(function () {}).then(loadSettings);
    }).catch(function () {});
  }

  function makeStepper(id, key, min, max) {
    var input = $(id);
    var timer = null;

    function clamp(v) {
      v = Math.round(Number(v));
      if (!isFinite(v)) v = current[key] || min;
      return Math.min(max, Math.max(min, v));
    }

    function put(v) {
      var patch = {};
      patch[key] = v;
      putSettings(patch);
    }

    function step(delta) {
      var v = clamp(Number(input.value) + delta);
      input.value = v;
      clearTimeout(timer);
      timer = setTimeout(function () { put(v); }, STEP_DEBOUNCE_MS);
    }

    holdStep($(id + "-minus"), function () { step(-1); });
    holdStep($(id + "-plus"), function () { step(1); });
    input.addEventListener("input", function () {
      var raw = input.value;
      if (raw === "") return;
      var v = clamp(raw);
      clearTimeout(timer);
      timer = setTimeout(function () {
        input.value = v;
        put(v);
      }, STEP_DEBOUNCE_MS);
    });
    input.addEventListener("change", function () {
      if (input.value === "") input.value = current[key];
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

  function setSetting(key, value) {
    var patch = {};
    patch[key] = value;
    var next = {};
    for (var k in current) next[k] = current[k];
    next[key] = value;
    renderSettings(next);
    putSettings(patch);
  }

  function onCheckChange(e) {
    setSetting(e.currentTarget.getAttribute("data-key"), e.currentTarget.checked);
  }

  function onRadioChange(e) {
    var el = e.currentTarget;
    if (!el.checked) return;
    var name = el.getAttribute("name");
    var flag = name === "clock_on_top" || name === "date_on_top" || name === "cover_on_top";
    setSetting(name, flag ? el.value === "true" : el.value);
  }

  function onBrightnessInput() {
    sliding = true;
    $("brightness-value").textContent = clampBrightness($("brightness").value) + "%";
  }

  function onBrightnessChange() {
    sliding = false;
    var v = clampBrightness($("brightness").value);
    showBrightness(v);
    if (v === current.brightness) return;
    putSettings({ brightness: v });
  }

  function openRestart() {
    $("restart-error").hidden = true;
    $("restart-dialog").hidden = false;
    $("restart-yes").focus();
  }

  function closeRestart() {
    $("restart-dialog").hidden = true;
    $("restart").focus();
  }

  function restartBoard() {
    if (rebootBusy) return;
    rebootBusy = true;
    $("restart-yes").disabled = true;
    fetch("/api/sound/reboot", { method: "POST" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      closeRestart();
      $("display-state").textContent = "Home Board is restarting...";
    }).catch(function (err) {
      $("restart-error").textContent = err.message === "503"
        ? "Helper not running. The board was not restarted."
        : "Could not restart the board. Try again.";
      $("restart-error").hidden = false;
    }).then(function () {
      rebootBusy = false;
      $("restart-yes").disabled = false;
    });
  }

  function loadStatus() {
    getJSON("/api/status").then(function (s) {
      $("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
    }).catch(function () {
      $("display-state").textContent = "Server not reachable";
    });
  }

  function onSettingsChanged() {
    loadSettings().catch(function () {});
  }

  var backoff = 1;
  var everConnected = false;

  function retryEvents() {
    setTimeout(connectEvents, backoff * 1000);
    backoff = Math.min(backoff * 2, MAX_BACKOFF_S);
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
    es.addEventListener("hello", function () {
      if (everConnected) onSettingsChanged();
      everConnected = true;
    });
    es.addEventListener("settings_changed", onSettingsChanged);
    es.onerror = function () {
      es.close();
      retryEvents();
    };
  }

  function init() {
    makeStepper("interval", "interval_s", 5, 600);
    $("channel-timeout").addEventListener("change", function (e) {
      putSettings({ channel_timeout_min: parseInt(e.target.value, 10) });
    });
    $("weather-location").addEventListener("change", saveWeather);
    $("weather-location").addEventListener("keydown", function (e) {
      if (e.key === "Enter") e.target.blur();
    });
    makeStepper("clock-size", "clock_size", 2, 12);
    makeStepper("caption-size", "caption_size", 2, 12);
    makeStepper("date-size", "date_size", 2, 12);
    makeStepper("cover-size", "cover_size", 2, 12);
    RADIO_KEYS.forEach(function (key) {
      var radios = document.querySelectorAll('input[name="' + key + '"]');
      for (var j = 0; j < radios.length; j++) radios[j].addEventListener("change", onRadioChange);
    });
    var checks = document.querySelectorAll(".checks input[data-key]");
    for (var n = 0; n < checks.length; n++) checks[n].addEventListener("change", onCheckChange);
    $("brightness").addEventListener("input", onBrightnessInput);
    $("brightness").addEventListener("change", onBrightnessChange);
    $("restart").addEventListener("click", openRestart);
    $("restart-yes").addEventListener("click", restartBoard);
    $("restart-no").addEventListener("click", closeRestart);
    $("restart-close").addEventListener("click", closeRestart);
    $("restart-backdrop").addEventListener("click", closeRestart);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("restart-dialog").hidden) closeRestart();
    });
    loadSettings().catch(function () {
      showSettingsError("Could not load settings");
    });
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
    connectEvents();
  }

  init();
})();
