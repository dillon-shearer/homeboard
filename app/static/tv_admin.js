(function (root) {
  "use strict";

  var COLS = 2;
  var ROWS = 2;

  function fits(widgets, widget, col, row, w, h) {
    if (col < 0 || row < 0 || col + w > COLS || row + h > ROWS) return false;
    return widgets.every(function (o) {
      if (widget && o.id === widget.id) return true;
      return col + w <= o.col || o.col + o.w <= col || row + h <= o.row || o.row + o.h <= row;
    });
  }

  var TvAdmin = { fits: fits };
  root.TvAdmin = TvAdmin;
  if (typeof module !== "undefined" && module.exports) module.exports = TvAdmin;
  if (typeof document === "undefined") return;

  var STATUS_MS = 10000;
  var MAX_BACKOFF_S = 30;
  var STEP_DEBOUNCE_MS = 400;
  var UP = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="6,2 11,9 1,9" fill="#000"/></svg>';
  var DOWN = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="1,3 11,3 6,10" fill="#000"/></svg>';
  var RADIO_KEYS = ["clock_pos", "caption_pos", "clock_on_top"];

  var screens = [];
  var shown = { channel: null, screen_id: null };
  var editing = false;
  var loaded = false;
  var current = {};
  var win = null;
  var lists = [];
  var allowed = {};
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
    if (!$("channel-timeout")) return;
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
    add.hidden = !(editing || (loaded && screens.length === 0));
    row.classList.toggle("editing", editing);
    $("screens-hint").hidden = editing;
    edit.textContent = editing ? "Done" : "Edit";
    edit.setAttribute("aria-pressed", editing ? "true" : "false");
    showChannel(shown.channel);
  }

  function busy() {
    var named = $("name-dialog");
    return win !== null || !!(named && !named.hidden);
  }

  function setScreens(list) {
    if (!Array.isArray(list) || busy()) return;
    var next = list.map(function (s) { return { id: s.id, name: s.name }; });
    if (loaded && JSON.stringify(next) === JSON.stringify(screens)) return;
    loaded = true;
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
    $("screens-new").focus();
    return loadScreens();
  }

  function saveName(e) {
    e.preventDefault();
    var name = $("name-input").value.trim();
    if (!name) {
      $("name-input").focus();
      return;
    }
    send("POST", "/api/screens", { name: name }).then(function (created) {
      return closeName().then(function () { openScreen(created); });
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

  function fitting(kind, widget, col, row) {
    return (allowed[kind] || []).filter(function (s) {
      return fits(win.widgets, widget, col, row, s[0], s[1]);
    }).map(function (s) { return s[0] + "x" + s[1]; });
  }

  function addKinds(col, row) {
    return Object.keys(KIND_NAMES).filter(function (kind) { return fitting(kind, null, col, row).length > 0; });
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
      b.addEventListener("click", function () { tapWidget(wd); });
      for (var dc = 0; dc < wd.w; dc++) {
        for (var dr = 0; dr < wd.h; dr++) taken[(wd.col + dc) + "," + (wd.row + dr)] = true;
      }
      box.appendChild(b);
    });
    for (var r = 0; r < ROWS; r++) {
      for (var c = 0; c < COLS; c++) {
        if (taken[c + "," + r]) continue;
        var moving = win.selected !== null;
        if (!moving && !addKinds(c, r).length) continue;
        var picked = !moving && win.cell !== null && win.cell[0] === c && win.cell[1] === r;
        var add = cell(picked ? "cell empty on" : moving ? "cell empty target" : "cell empty", moving ? "Move here" : "+");
        if (!moving) {
          add.setAttribute("aria-label", "Add widget");
          add.setAttribute("aria-pressed", picked ? "true" : "false");
        }
        add.setAttribute("data-cell", c + "," + r);
        add.style.gridColumn = String(c + 1);
        add.style.gridRow = String(r + 1);
        add.addEventListener("click", tapCell.bind(null, c, r));
        box.appendChild(add);
      }
    }
    renderWidgetRow();
  }

  function renderWidgetRow() {
    var i = selectedIndex();
    var wd = win.widgets[i];
    $("widget-row").hidden = !wd;
    $("widget-add").hidden = win.cell === null;
    if (win.cell !== null) {
      var kinds = addKinds(win.cell[0], win.cell[1]);
      var buttons = document.querySelectorAll("[data-kind]");
      for (var k = 0; k < buttons.length; k++) buttons[k].hidden = kinds.indexOf(buttons[k].getAttribute("data-kind")) === -1;
    }
    if (!wd) return;
    var size = $("widget-size");
    size.textContent = "";
    var sizes = fitting(wd.kind, wd, wd.col, wd.row);
    if (sizes.indexOf(wd.w + "x" + wd.h) === -1) sizes.push(wd.w + "x" + wd.h);
    sizes.forEach(function (s) { size.appendChild(option(s, SIZE_NAMES[s])); });
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
  }

  function applyScreen(s, selected) {
    if (!win || !s) return;
    win.widgets = Array.isArray(s.widgets) ? s.widgets : [];
    if (selected !== undefined) win.selected = selected;
    if (selectedIndex() === -1) win.selected = null;
    if (win.cell !== null && !addKinds(win.cell[0], win.cell[1]).length) win.cell = null;
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

  function windowCall(method, url, body, msg, pickNew, fitMsg) {
    var ed = win;
    var before = win.widgets.map(function (wd) { return wd.id; });
    showScreenError("");
    return send(method, url, body).then(function (s) {
      if (win !== ed) return;
      if (!s || !Array.isArray(s.widgets)) return refreshScreen();
      var added = pickNew ? s.widgets.filter(function (wd) { return before.indexOf(wd.id) === -1; })[0] : null;
      applyScreen(s, added ? added.id : undefined);
    }).catch(function (err) {
      if (win !== ed) return;
      showScreenError(fitMsg && err.status === 422 ? fitMsg : msg);
      refreshScreen();
    });
  }

  function openScreen(screen) {
    win = { id: screen.id, name: screen.name, widgets: [], selected: null, cell: null };
    $("screen-name").value = screen.name;
    showScreenError("");
    $("screen-layout").textContent = "";
    $("widget-row").hidden = true;
    $("widget-add").hidden = true;
    $("screen-delete-dialog").hidden = true;
    $("screen-dialog").hidden = false;
    var ed = win;
    getJSON("/api/widget-sizes").then(function (data) {
      if (data && typeof data === "object") allowed = data;
    }).catch(function () {}).then(function () {
      return getJSON("/api/lists");
    }).then(function (data) {
      if (Array.isArray(data)) lists = data.map(function (l) { return { id: l.id, name: l.name }; });
    }).catch(function () {}).then(function () {
      if (win === ed) return refreshScreen();
    });
  }

  function closeScreen() {
    $("screen-dialog").hidden = true;
    $("screen-delete-dialog").hidden = true;
    var id = win ? win.id : null;
    win = null;
    return loadScreens().then(function () {
      if (win) return;
      var back = editing ? $("screens-new") : $("screens-edit");
      var groups = $("tv-stops").querySelectorAll("[data-edit-screen]");
      for (var i = 0; i < groups.length; i++) {
        if (groups[i].getAttribute("data-edit-screen") === String(id)) back = groups[i].children[1];
      }
      back.focus();
    });
  }

  function tapWidget(wd) {
    if (win.selected === wd.id) win.selected = null;
    else if (win.selected !== null) {
      var me = win.widgets[selectedIndex()];
      windowCall("PATCH", "/api/widgets/" + me.id, { col: wd.col, row: wd.row }, "Could not move the widget. Try again.");
      return;
    } else win.selected = wd.id;
    win.cell = null;
    renderLayout();
  }

  function tapCell(col, row) {
    if (win.selected !== null) {
      var me = win.widgets[selectedIndex()];
      windowCall("PATCH", "/api/widgets/" + me.id, { col: col, row: row }, "Could not move the widget. Try again.");
      return;
    }
    var same = win.cell !== null && win.cell[0] === col && win.cell[1] === row;
    win.cell = same ? null : [col, row];
    renderLayout();
  }

  function addWidget(e) {
    if (!win || win.cell === null) return;
    var kind = e.currentTarget.getAttribute("data-kind");
    var at = win.cell;
    var body = { kind: kind, col: at[0], row: at[1] };
    if (kind === "lists" && fitting(kind, null, at[0], at[1])[0] === "1x1" && lists.length) body.list_id = lists[0].id;
    win.cell = null;
    windowCall("POST", "/api/screens/" + win.id + "/widgets", body, "Could not add the widget. Try again.", true);
  }

  function onSizeChange() {
    var wd = win && win.widgets[selectedIndex()];
    if (!wd) return;
    var parts = $("widget-size").value.split("x");
    windowCall("PATCH", "/api/widgets/" + wd.id, { w: Number(parts[0]), h: Number(parts[1]) }, "Could not change the size. Try again.", false, "That size does not fit here.");
  }

  function onListChange() {
    var wd = win && win.widgets[selectedIndex()];
    var v = $("widget-list").value;
    if (!wd || !v) return;
    windowCall("PATCH", "/api/widgets/" + wd.id, { list_id: Number(v) }, "Could not pick that list. Try again.");
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
    if (win) $("screen-delete").focus();
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
    if ($("tv-stops")) initScreens();
    if ($("channel-timeout")) initSettings();
    loadSettings().catch(function () {
      showSettingsError("Could not load settings");
    });
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
    connectEvents();
  }

  function initScreens() {
    var buttons = document.querySelectorAll("[data-channel]");
    for (var i = 0; i < buttons.length; i++) buttons[i].addEventListener("click", pick);
    $("screens-edit").addEventListener("click", function () {
      editing = !editing;
      render();
    });
    $("screens-new").addEventListener("click", openName);
    $("name-form").addEventListener("submit", saveName);
    $("name-cancel").addEventListener("click", closeName);
    $("name-close").addEventListener("click", closeName);
    $("name-backdrop").addEventListener("click", closeName);
    $("screen-form").addEventListener("submit", saveScreen);
    $("screen-close").addEventListener("click", closeScreen);
    $("screen-backdrop").addEventListener("click", closeScreen);
    $("screen-delete").addEventListener("click", openDeleteScreen);
    $("screen-confirm-delete").addEventListener("click", deleteScreen);
    $("screen-confirm-cancel").addEventListener("click", closeDeleteScreen);
    $("screen-delete-close").addEventListener("click", closeDeleteScreen);
    $("screen-delete-backdrop").addEventListener("click", closeDeleteScreen);
    $("widget-size").addEventListener("change", onSizeChange);
    $("widget-list").addEventListener("change", onListChange);
    $("widget-remove").addEventListener("click", removeWidget);
    var kinds = document.querySelectorAll("[data-kind]");
    for (var k = 0; k < kinds.length; k++) kinds[k].addEventListener("click", addWidget);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("screen-delete-dialog").hidden) closeDeleteScreen();
      else if (!$("name-dialog").hidden) closeName();
      else if (!$("screen-dialog").hidden) closeScreen();
    });
    loadScreens();
    setInterval(loadScreens, STATUS_MS);
  }

  function initSettings() {
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
  }

  init();
})(typeof window !== "undefined" ? window : globalThis);
