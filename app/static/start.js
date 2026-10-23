(function () {
  "use strict";

  var STATUS_MS = 10000;
  var UP = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="6,2 11,9 1,9" fill="#000"/></svg>';
  var DOWN = '<svg viewBox="0 0 12 12" aria-hidden="true"><polygon points="1,3 11,3 6,10" fill="#000"/></svg>';

  var screens = [];
  var shown = { channel: null, screen_id: null };
  var editing = false;
  var renameId = null;

  function $(id) {
    return document.getElementById(id);
  }

  function showChannel(channel, screenId) {
    shown = { channel: channel, screen_id: screenId === undefined ? shown.screen_id : screenId };
    var current = shown.screen_id;
    var known = screens.some(function (s) { return s.id === current; });
    if (!known && screens.length) current = screens[0].id;
    var buttons = document.querySelectorAll("[data-channel]");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].getAttribute("data-channel") === channel;
      if (on && channel === "screen") on = buttons[i].getAttribute("data-screen-id") === String(current);
      buttons[i].classList.toggle("on", on);
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function putSettings(body) {
    return fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      showChannel(s.channel, s.screen_id);
      $("channel-timeout").value = String(s.channel_timeout_min);
    }).catch(function () {
      $("display-state").textContent = "Server not reachable";
    });
  }

  function send(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
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
    name.addEventListener("click", function () { openRename(screen); });
    group.appendChild(up);
    group.appendChild(name);
    group.appendChild(down);
    return group;
  }

  function render() {
    var row = $("tv-stops");
    var edit = $("screens-edit");
    var old = row.querySelectorAll("[data-screen-id], [data-edit-screen]");
    for (var i = 0; i < old.length; i++) row.removeChild(old[i]);
    screens.forEach(function (screen, n) {
      row.insertBefore(editing ? editGroup(screen, n) : screenButton(screen), edit);
    });
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
    fetch("/api/screens", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(setScreens).catch(function () {});
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

  function openRename(screen) {
    renameId = screen.id;
    $("name-input").value = screen.name;
    $("name-error").hidden = true;
    $("name-dialog").hidden = false;
    $("name-input").focus();
  }

  function closeRename() {
    $("name-dialog").hidden = true;
    renameId = null;
  }

  function saveRename(e) {
    e.preventDefault();
    var name = $("name-input").value.trim();
    if (!name || renameId === null) {
      $("name-input").focus();
      return;
    }
    send("PATCH", "/api/screens/" + renameId, { name: name }).then(function () {
      closeRename();
      loadScreens();
    }).catch(function () {
      $("name-error").hidden = false;
    });
  }

  function loadSettings() {
    fetch("/api/settings", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      showChannel(s.channel, s.screen_id);
      $("channel-timeout").value = String(s.channel_timeout_min);
    }).catch(function () {});
  }

  function loadStatus() {
    fetch("/api/status", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      var n = s.photo_count;
      $("photo-count").textContent = n + (n === 1 ? " photo" : " photos");
      $("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
      if (s.channel) showChannel(s.channel, s.screen_id);
    }).catch(function () {
      $("display-state").textContent = "Server not reachable";
    });
  }

  var buttons = document.querySelectorAll("[data-channel]");
  for (var i = 0; i < buttons.length; i++) buttons[i].addEventListener("click", pick);
  $("screens-edit").addEventListener("click", function () {
    editing = !editing;
    render();
  });
  $("channel-timeout").addEventListener("change", function (e) {
    putSettings({ channel_timeout_min: parseInt(e.target.value, 10) });
  });
  $("name-form").addEventListener("submit", saveRename);
  $("name-cancel").addEventListener("click", closeRename);
  $("name-close").addEventListener("click", closeRename);
  $("name-backdrop").addEventListener("click", closeRename);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("name-dialog").hidden) closeRename();
  });

  loadSettings();
  loadScreens();
  loadStatus();
  setInterval(loadStatus, STATUS_MS);
  setInterval(loadScreens, STATUS_MS);
})();
