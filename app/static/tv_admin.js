(function (root) {
  "use strict";

  var COLS = 2;
  var ROWS = 2;
  var SIZES = [[2, 2], [2, 1], [1, 2], [1, 1]];

  function pack(widgets) {
    var taken = {};
    var placed = [];
    for (var i = 0; i < widgets.length; i++) {
      var w = widgets[i].w;
      var h = widgets[i].h;
      var spot = null;
      for (var r = 0; r < ROWS && !spot; r++) {
        for (var c = 0; c < COLS && !spot; c++) if (!taken[c + "," + r]) spot = [c, r];
      }
      if (!spot || spot[0] + w > COLS || spot[1] + h > ROWS) return null;
      for (var dc = 0; dc < w; dc++) {
        for (var dr = 0; dr < h; dr++) {
          var key = (spot[0] + dc) + "," + (spot[1] + dr);
          if (taken[key]) return null;
          taken[key] = true;
        }
      }
      placed.push({ id: widgets[i].id, col: spot[0], row: spot[1], w: w, h: h });
    }
    return placed;
  }

  function sizeOptions(widgets, index) {
    return SIZES.filter(function (s) {
      return pack(widgets.map(function (wd, i) {
        return i === index ? { id: wd.id, w: s[0], h: s[1] } : wd;
      })) !== null;
    }).map(function (s) { return s[0] + "x" + s[1]; });
  }

  function addSize(widgets) {
    for (var i = 0; i < SIZES.length; i++) {
      if (pack(widgets.concat([{ id: null, w: SIZES[i][0], h: SIZES[i][1] }]))) return SIZES[i][0] + "x" + SIZES[i][1];
    }
    return null;
  }

  var TvAdmin = { SIZES: SIZES, pack: pack, sizeOptions: sizeOptions, addSize: addSize };
  root.TvAdmin = TvAdmin;
  if (typeof module !== "undefined" && module.exports) module.exports = TvAdmin;
  if (typeof document === "undefined") return;

  var STATUS_MS = 10000;
  var STEP_DEBOUNCE_MS = 400;
  var UP = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="6,2 11,9 1,9" fill="#000"/></svg>';
  var DOWN = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="1,3 11,3 6,10" fill="#000"/></svg>';
  var RADIO_KEYS = ["clock_pos", "caption_pos", "clock_on_top"];

  var screens = [];
  var shown = { channel: null, screen_id: null };
  var editing = false;
  var current = {};
  var win = null;
  var lists = [];
  var KIND_NAMES = { clock: "Clock", weather: "Weather", calendar: "Calendar", lists: "Lists" };
  var SIZE_NAMES = { "2x2": "Full (2x2)", "2x1": "Wide (2x1)", "1x2": "Tall (1x2)", "1x1": "Quarter (1x1)" };

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
    var name = button("w95-btn", "", "Edit " + screen.name);
    var down = button("w95-btn move", DOWN, "Move " + screen.name + " down");
    name.textContent = screen.name;
    up.disabled = i === 0;
    down.disabled = i === screens.length - 1;
    up.addEventListener("click", function () { move(i, -1); });
    down.addEventListener("click", function () { move(i, 1); });
    name.addEventListener("click", function () { openScreen(screen); });
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

  function busy() {
    return win !== null;
  }

  function setScreens(list) {
    if (!Array.isArray(list) || busy()) return;
    var next = list.map(function (s) { return { id: s.id, name: s.name }; });
    if (JSON.stringify(next) === JSON.stringify(screens)) return;
    screens = next;
    render();
  }

  function loadScreens() {
    if (busy()) return Promise.resolve();
    return getJSON("/api/screens").then(setScreens).catch(function () {});
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

  function openName() {
    $("name-input").value = "";
    $("name-error").hidden = true;
    $("name-dialog").hidden = false;
    $("name-input").focus();
  }

  function closeName() {
    $("name-dialog").hidden = true;
  }

  function saveName(e) {
    e.preventDefault();
    var name = $("name-input").value.trim();
    if (!name) {
      $("name-input").focus();
      return;
    }
    send("POST", "/api/screens", { name: name }).then(function (created) {
      closeName();
      return loadScreens().then(function () { openScreen(created); });
    }).catch(function () {
      $("name-error").hidden = false;
    });
  }

  function widgetLabel(wd) {
    if (wd.kind === "lists" && wd.w === 1 && wd.h === 1) {
      for (var i = 0; i < lists.length; i++) if (lists[i].id === wd.list_id) return lists[i].name;
    }
    return KIND_NAMES[wd.kind] || wd.kind;
  }

  function selectedIndex() {
    for (var i = 0; i < win.widgets.length; i++) if (win.widgets[i].id === win.selected) return i;
    return -1;
  }

  function swapped(i, j) {
    var out = win.widgets.slice();
    var t = out[i];
    out[i] = out[j];
    out[j] = t;
    return out;
  }

  function canMove(i, step) {
    var j = i + step;
    return j >= 0 && j < win.widgets.length && pack(swapped(i, j)) !== null;
  }

  function showScreenError(msg) {
    $("screen-error").textContent = msg || "";
    $("screen-error").hidden = !msg;
  }

  function option(value, text) {
    var o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    return o;
  }

  function cell(cls, text) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = text;
    return b;
  }

  function renderLayout() {
    var box = $("screen-layout");
    box.textContent = "";
    var taken = {};
    win.widgets.forEach(function (wd) {
      var on = wd.id === win.selected;
      var b = cell(on ? "cell on" : "cell", widgetLabel(wd));
      b.setAttribute("data-widget-id", String(wd.id));
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.style.gridColumn = (wd.col + 1) + " / span " + wd.w;
      b.style.gridRow = (wd.row + 1) + " / span " + wd.h;
      b.addEventListener("click", function () { selectWidget(wd.id); });
      for (var dc = 0; dc < wd.w; dc++) {
        for (var dr = 0; dr < wd.h; dr++) taken[(wd.col + dc) + "," + (wd.row + dr)] = true;
      }
      box.appendChild(b);
    });
    for (var r = 0; r < ROWS; r++) {
      for (var c = 0; c < COLS; c++) {
        if (taken[c + "," + r]) continue;
        var add = cell("cell empty", "+");
        add.setAttribute("aria-label", "Add widget");
        add.setAttribute("data-cell", c + "," + r);
        add.style.gridColumn = String(c + 1);
        add.style.gridRow = String(r + 1);
        add.addEventListener("click", openAdd);
        box.appendChild(add);
      }
    }
    renderWidgetRow();
  }

  function renderWidgetRow() {
    var i = selectedIndex();
    var wd = win.widgets[i];
    $("widget-row").hidden = !wd;
    $("widget-add").hidden = !win.adding;
    if (!wd) return;
    var size = $("widget-size");
    size.textContent = "";
    sizeOptions(win.widgets, i).forEach(function (s) { size.appendChild(option(s, SIZE_NAMES[s])); });
    size.value = wd.w + "x" + wd.h;
    var single = wd.kind === "lists" && wd.w === 1 && wd.h === 1;
    $("widget-list-field").hidden = !single;
    if (single) {
      var sel = $("widget-list");
      sel.textContent = "";
      var known = lists.some(function (l) { return l.id === wd.list_id; });
      if (!known) sel.appendChild(option("", "Pick a list"));
      lists.forEach(function (l) { sel.appendChild(option(String(l.id), l.name)); });
      sel.value = known ? String(wd.list_id) : "";
    }
    $("widget-left").disabled = !canMove(i, -1);
    $("widget-right").disabled = !canMove(i, 1);
  }

  function applyScreen(s, selected) {
    if (!win || !s) return;
    win.widgets = Array.isArray(s.widgets) ? s.widgets : [];
    if (selected !== undefined) win.selected = selected;
    if (selectedIndex() === -1) win.selected = null;
    renderLayout();
  }

  function refreshScreen() {
    var ed = win;
    return getJSON("/api/screens").then(function (all) {
      if (win !== ed) return;
      var s = all.filter(function (x) { return x.id === ed.id; })[0];
      if (!s) {
        closeScreen();
        return;
      }
      applyScreen(s);
    }).catch(function () {
      if (win === ed) showScreenError("Could not load the screen. Try again.");
    });
  }

  function windowCall(method, url, body, msg, pickNew) {
    var ed = win;
    var before = win.widgets.map(function (wd) { return wd.id; });
    showScreenError("");
    return send(method, url, body).then(function (s) {
      if (win !== ed) return;
      if (!s || !Array.isArray(s.widgets)) return refreshScreen();
      var added = pickNew ? s.widgets.filter(function (wd) { return before.indexOf(wd.id) === -1; })[0] : null;
      applyScreen(s, added ? added.id : undefined);
    }).catch(function () {
      if (win !== ed) return;
      showScreenError(msg);
      refreshScreen();
    });
  }

  function openScreen(screen) {
    win = { id: screen.id, name: screen.name, widgets: [], selected: null, adding: false };
    $("screen-name").value = screen.name;
    showScreenError("");
    $("screen-layout").textContent = "";
    $("widget-row").hidden = true;
    $("widget-add").hidden = true;
    $("screen-delete-dialog").hidden = true;
    $("screen-dialog").hidden = false;
    var ed = win;
    getJSON("/api/lists").then(function (data) {
      if (Array.isArray(data)) lists = data.map(function (l) { return { id: l.id, name: l.name }; });
    }).catch(function () {}).then(function () {
      if (win === ed) return refreshScreen();
    });
  }

  function closeScreen() {
    $("screen-dialog").hidden = true;
    $("screen-delete-dialog").hidden = true;
    win = null;
    return loadScreens();
  }

  function selectWidget(id) {
    win.selected = id;
    win.adding = false;
    renderLayout();
  }

  function openAdd() {
    win.selected = null;
    win.adding = true;
    renderLayout();
  }

  function addWidget(e) {
    if (!win) return;
    var kind = e.currentTarget.getAttribute("data-kind");
    var body = { kind: kind };
    if (kind === "lists" && addSize(win.widgets) === "1x1" && lists.length) body.list_id = lists[0].id;
    win.adding = false;
    windowCall("POST", "/api/screens/" + win.id + "/widgets", body, "Could not add the widget. Try again.", true);
  }

  function onSizeChange() {
    var wd = win && win.widgets[selectedIndex()];
    if (!wd) return;
    var parts = $("widget-size").value.split("x");
    windowCall("PATCH", "/api/widgets/" + wd.id, { w: Number(parts[0]), h: Number(parts[1]) }, "Could not change the size. Try again.");
  }

  function onListChange() {
    var wd = win && win.widgets[selectedIndex()];
    var v = $("widget-list").value;
    if (!wd || !v) return;
    windowCall("PATCH", "/api/widgets/" + wd.id, { list_id: Number(v) }, "Could not pick that list. Try again.");
  }

  function moveWidget(step) {
    if (!win) return;
    var i = selectedIndex();
    if (i === -1 || !canMove(i, step)) return;
    var ids = swapped(i, i + step).map(function (wd) { return wd.id; });
    windowCall("PUT", "/api/screens/" + win.id + "/widgets/order", { ids: ids }, "Could not move the widget. Try again.");
  }

  function removeWidget() {
    var wd = win && win.widgets[selectedIndex()];
    if (!wd) return;
    win.selected = null;
    windowCall("DELETE", "/api/widgets/" + wd.id, undefined, "Could not remove the widget. Try again.");
  }

  function saveScreen(e) {
    e.preventDefault();
    if (!win) return;
    var name = $("screen-name").value.trim();
    if (!name) {
      $("screen-name").focus();
      return;
    }
    if (name === win.name) {
      closeScreen();
      return;
    }
    var ed = win;
    send("PATCH", "/api/screens/" + win.id, { name: name }).then(function () {
      if (win === ed) closeScreen();
    }).catch(function () {
      if (win === ed) showScreenError("Could not save the name. Try again.");
    });
  }

  function openDeleteScreen() {
    if (!win) return;
    $("screen-delete-text").textContent = "Delete \"" + win.name + "\" and its widgets? The TV goes back to Photos if it is showing it.";
    $("screen-delete-dialog").hidden = false;
    $("screen-confirm-delete").focus();
  }

  function closeDeleteScreen() {
    $("screen-delete-dialog").hidden = true;
  }

  function deleteScreen() {
    if (!win) return;
    var ed = win;
    closeDeleteScreen();
    send("DELETE", "/api/screens/" + win.id).then(function () {
      if (win === ed) closeScreen();
      loadSettings().catch(function () {});
    }).catch(function () {
      if (win === ed) showScreenError("Could not delete the screen. Try again.");
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
    $("screens-new").addEventListener("click", openName);
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
    $("screen-form").addEventListener("submit", saveScreen);
    $("screen-cancel").addEventListener("click", closeScreen);
    $("screen-close").addEventListener("click", closeScreen);
    $("screen-backdrop").addEventListener("click", closeScreen);
    $("screen-delete").addEventListener("click", openDeleteScreen);
    $("screen-confirm-delete").addEventListener("click", deleteScreen);
    $("screen-confirm-cancel").addEventListener("click", closeDeleteScreen);
    $("screen-delete-close").addEventListener("click", closeDeleteScreen);
    $("screen-delete-backdrop").addEventListener("click", closeDeleteScreen);
    $("widget-size").addEventListener("change", onSizeChange);
    $("widget-list").addEventListener("change", onListChange);
    $("widget-left").addEventListener("click", function () { moveWidget(-1); });
    $("widget-right").addEventListener("click", function () { moveWidget(1); });
    $("widget-remove").addEventListener("click", removeWidget);
    var kinds = document.querySelectorAll("[data-kind]");
    for (var k = 0; k < kinds.length; k++) kinds[k].addEventListener("click", addWidget);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("screen-delete-dialog").hidden) closeDeleteScreen();
      else if (!$("name-dialog").hidden) closeName();
      else if (!$("screen-dialog").hidden) closeScreen();
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
})(typeof window !== "undefined" ? window : globalThis);
