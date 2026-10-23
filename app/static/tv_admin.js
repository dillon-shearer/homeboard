(function () {
  "use strict";

  var STATUS_MS = 10000;
  var STEP_DEBOUNCE_MS = 400;
  var UP = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="6,2 11,9 1,9" fill="#000"/></svg>';
  var DOWN = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="1,3 11,3 6,10" fill="#000"/></svg>';
  var RADIO_KEYS = ["clock_pos", "caption_pos", "clock_on_top"];

  var screens = [];
  var shown = { channel: null, screen_id: null };
  var editing = false;
  var renameId = null;
  var current = {};

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
        if (!r.ok) throw new Error(typeof out.detail === "string" ? out.detail : "Could not save");
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

  function showChannel(channel, screenId) {
    shown = { channel: channel, screen_id: screenId === undefined ? shown.screen_id : screenId };
    var target = shown.screen_id;
    var known = screens.some(function (s) { return s.id === target; });
    if (!known && screens.length) target = screens[0].id;
    var buttons = document.querySelectorAll("[data-channel]");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].getAttribute("data-channel") === channel;
      if (on && channel === "screen") on = buttons[i].getAttribute("data-screen-id") === String(target);
      buttons[i].classList.toggle("on", on);
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function renderWeather(s) {
    var input = $("weather-location");
    if (document.activeElement !== input) input.value = s.weather_location || "";
    var note = $("weather-name");
    if (!s.weather_location) note.textContent = "";
    else note.textContent = s.weather_name ? "Showing weather for " + s.weather_name : "Looking up...";
  }

  function renderSettings(s) {
    current = s;
    showChannel(s.channel, s.screen_id);
    $("channel-timeout").value = String(s.channel_timeout_min);
    renderWeather(s);
    $("clock-size").value = s.clock_size;
    $("caption-size").value = s.caption_size;
    RADIO_KEYS.forEach(function (key) {
      var radios = document.querySelectorAll('input[name="' + key + '"]');
      for (var i = 0; i < radios.length; i++) radios[i].checked = radios[i].value === String(s[key]);
    });
    $("stack-row").hidden = s.clock_pos !== s.caption_pos;
    var checks = document.querySelectorAll(".checks input[data-key]");
    for (var n = 0; n < checks.length; n++) checks[n].checked = !!s[checks[n].getAttribute("data-key")];
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

  function button(cls, html, label) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.innerHTML = html;
    if (label) b.setAttribute("aria-label", label);
    return b;
  }

  function pick(e) {
    var b = e.currentTarget;
    var channel = b.getAttribute("data-channel");
    var body = { channel: channel };
    if (channel === "screen") body.screen_id = parseInt(b.getAttribute("data-screen-id"), 10);
    showChannel(channel, body.screen_id);
    putSettings(body);
  }

  function screenButton(screen) {
    var b = button("w95-btn", "", null);
    b.textContent = screen.name;
    b.setAttribute("data-channel", "screen");
    b.setAttribute("data-screen-id", String(screen.id));
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", pick);
    return b;
  }

  function editGroup(screen, i) {
    var group = document.createElement("span");
    group.className = "stop-edit";
    group.setAttribute("data-edit-screen", String(screen.id));
    var up = button("w95-btn move", UP, "Move " + screen.name + " up");
    var name = button("w95-btn", "", "Rename " + screen.name);
    var down = button("w95-btn move", DOWN, "Move " + screen.name + " down");
    name.textContent = screen.name;
    up.disabled = i === 0;
    down.disabled = i === screens.length - 1;
    up.addEventListener("click", function () { move(i, -1); });
    down.addEventListener("click", function () { move(i, 1); });
    name.addEventListener("click", function () { openName(screen); });
    group.appendChild(up);
    group.appendChild(name);
    group.appendChild(down);
    return group;
  }

  function render() {
    var row = $("tv-stops");
    var add = $("screens-new");
    var edit = $("screens-edit");
    var old = row.querySelectorAll("[data-screen-id], [data-edit-screen]");
    for (var i = 0; i < old.length; i++) row.removeChild(old[i]);
    screens.forEach(function (screen, n) {
      row.insertBefore(editing ? editGroup(screen, n) : screenButton(screen), add);
    });
    add.hidden = !editing;
    edit.textContent = editing ? "Done" : "Edit";
    edit.setAttribute("aria-pressed", editing ? "true" : "false");
    showChannel(shown.channel);
  }

  function setScreens(list) {
    if (!Array.isArray(list)) return;
    var next = list.map(function (s) { return { id: s.id, name: s.name }; });
    if (JSON.stringify(next) === JSON.stringify(screens)) return;
    screens = next;
    render();
  }

  function loadScreens() {
    getJSON("/api/screens").then(setScreens).catch(function () {});
  }

  function move(i, step) {
    var j = i + step;
    if (j < 0 || j >= screens.length) return;
    var ids = screens.map(function (s) { return s.id; });
    var t = ids[i];
    ids[i] = ids[j];
    ids[j] = t;
    send("PUT", "/api/screens/order", { ids: ids }).then(setScreens).catch(function () {
      $("display-state").textContent = "Server not reachable";
      loadScreens();
    });
  }

  function openName(screen) {
    renameId = screen ? screen.id : null;
    $("name-title").textContent = screen ? "Rename" : "New screen";
    $("name-input").value = screen ? screen.name : "";
    $("name-error").hidden = true;
    $("name-dialog").hidden = false;
    $("name-input").focus();
  }

  function closeName() {
    $("name-dialog").hidden = true;
    renameId = null;
  }

  function saveName(e) {
    e.preventDefault();
    var name = $("name-input").value.trim();
    if (!name) {
      $("name-input").focus();
      return;
    }
    var req = renameId === null
      ? send("POST", "/api/screens", { name: name })
      : send("PATCH", "/api/screens/" + renameId, { name: name });
    req.then(function () {
      closeName();
      loadScreens();
    }).catch(function () {
      $("name-error").hidden = false;
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
    setSetting(name, name === "clock_on_top" ? el.value === "true" : el.value);
  }

  function loadStatus() {
    getJSON("/api/status").then(function (s) {
      $("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
      if (s.channel) showChannel(s.channel, s.screen_id);
    }).catch(function () {
      $("display-state").textContent = "Server not reachable";
    });
  }

  function init() {
    var buttons = document.querySelectorAll("[data-channel]");
    for (var i = 0; i < buttons.length; i++) buttons[i].addEventListener("click", pick);
    $("screens-edit").addEventListener("click", function () {
      editing = !editing;
      render();
    });
    $("screens-new").addEventListener("click", function () { openName(null); });
    $("channel-timeout").addEventListener("change", function (e) {
      putSettings({ channel_timeout_min: parseInt(e.target.value, 10) });
    });
    $("weather-location").addEventListener("change", saveWeather);
    $("weather-location").addEventListener("keydown", function (e) {
      if (e.key === "Enter") e.target.blur();
    });
    makeStepper("clock-size", "clock_size", 2, 12);
    makeStepper("caption-size", "caption_size", 2, 12);
    RADIO_KEYS.forEach(function (key) {
      var radios = document.querySelectorAll('input[name="' + key + '"]');
      for (var j = 0; j < radios.length; j++) radios[j].addEventListener("change", onRadioChange);
    });
    var checks = document.querySelectorAll(".checks input[data-key]");
    for (var n = 0; n < checks.length; n++) checks[n].addEventListener("change", onCheckChange);
    $("name-form").addEventListener("submit", saveName);
    $("name-cancel").addEventListener("click", closeName);
    $("name-close").addEventListener("click", closeName);
    $("name-backdrop").addEventListener("click", closeName);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("name-dialog").hidden) closeName();
    });

    loadSettings().catch(function () {
      showSettingsError("Could not load settings");
    });
    loadScreens();
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
    setInterval(loadScreens, STATUS_MS);
  }

  init();
})();
