(function (root) {
  "use strict";

  function moveId(ids, from, to) {
    var out = ids.slice();
    if (from < 0 || from >= out.length || to < 0 || to >= out.length) return out;
    var item = out.splice(from, 1)[0];
    out.splice(to, 0, item);
    return out;
  }

  var Admin = { moveId: moveId };
  root.Admin = Admin;
  if (typeof module !== "undefined" && module.exports) module.exports = Admin;
  if (typeof document === "undefined") return;

  var TYPES = ["image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"];
  var EXTS = ["jpg", "jpeg", "png", "heic", "heif", "webp"];
  var TYPE_ERROR = "Only JPEG, PNG, HEIC and WebP photos";
  var SERVER_ERRORS = {
    "too large": "This photo is too large",
    "cannot open": "This file could not be opened as a photo",
    "unsupported format": TYPE_ERROR,
    "processing failed": "The photo could not be processed",
    "save failed": "The photo could not be saved"
  };
  var STATUS_MS = 10000;
  var INTERVAL_DEBOUNCE_MS = 400;
  var TOAST_MS = 4000;
  var TOAST_FADE_MS = 400;

  function $(id) {
    return document.getElementById(id);
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function isSupported(file) {
    if (file.type) return TYPES.indexOf(file.type.toLowerCase()) !== -1;
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    return EXTS.indexOf(ext) !== -1;
  }

  function plainError(text) {
    if (!text) return "Upload failed";
    return SERVER_ERRORS[text] || text.charAt(0).toUpperCase() + text.slice(1);
  }

  function errorFromResponse(xhr) {
    var body = null;
    try {
      body = JSON.parse(xhr.responseText);
    } catch (e) {
      body = null;
    }
    if (body && body.errors && body.errors.length) return plainError(body.errors[0].error);
    if (body && typeof body.detail === "string") return plainError(body.detail);
    if (xhr.status === 413) return SERVER_ERRORS["too large"];
    return "Upload failed (error " + xhr.status + ")";
  }

  function setCount(n) {
    $("photo-count").textContent = n + (n === 1 ? " photo" : " photos");
  }

  function loadStatus() {
    return getJSON("/api/status").then(function (s) {
      var dot = $("status-dot");
      dot.classList.toggle("on", !!s.display_connected);
      dot.title = s.display_connected ? "Display connected" : "Display not connected";
      setCount(s.photo_count);
      $("host").textContent = s.hostname;
      $("ip").textContent = s.ip || "";
      $("disk").textContent = (s.disk_free_bytes / 1073741824).toFixed(1) + " GB free";
      $("version").textContent = "v" + s.version;
    }).catch(function () {
      var dot = $("status-dot");
      dot.classList.remove("on");
      dot.title = "Server not reachable";
    });
  }

  function loadGrid() {
    return getJSON("/api/photos?all=1").then(function (photos) {
      var grid = $("grid");
      grid.textContent = "";
      photos.forEach(function (p) {
        grid.appendChild(buildTile(p));
      });
      $("grid-empty").hidden = photos.length > 0;
      setCount(photos.length);
    });
  }

  function buildTile(p) {
    var tile = document.createElement("div");
    tile.className = "tile" + (p.enabled ? "" : " disabled");
    tile.setAttribute("data-id", p.id);
    tile.setAttribute("data-caption", p.caption || "");
    var img = document.createElement("img");
    img.src = p.thumb_url;
    img.alt = p.caption || "";
    img.loading = "lazy";
    img.draggable = false;
    tile.appendChild(img);

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tile-toggle";
    toggle.appendChild(document.createElement("span"));
    tile.appendChild(toggle);
    setToggle(tile, p.enabled);

    var menu = document.createElement("button");
    menu.type = "button";
    menu.className = "tile-menu";
    menu.setAttribute("aria-label", "Photo options");
    menu.setAttribute("aria-haspopup", "dialog");
    menu.textContent = "⋮";
    tile.appendChild(menu);

    var handle = document.createElement("span");
    handle.className = "tile-handle";
    handle.draggable = true;
    handle.title = "Drag to reorder";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "≡";
    tile.appendChild(handle);
    return tile;
  }

  function setToggle(tile, enabled) {
    tile.classList.toggle("disabled", !enabled);
    var btn = tile.querySelector(".tile-toggle");
    btn.setAttribute("aria-pressed", enabled ? "true" : "false");
    btn.setAttribute("aria-label", enabled ? "Shown on the board" : "Hidden from the board");
  }

  var toastTimer = null;

  function showError(msg) {
    var el = $("toast");
    clearTimeout(toastTimer);
    el.textContent = msg;
    el.classList.remove("fade");
    el.hidden = false;
    toastTimer = setTimeout(function () {
      el.classList.add("fade");
      toastTimer = setTimeout(function () {
        el.hidden = true;
        el.classList.remove("fade");
      }, TOAST_FADE_MS);
    }, TOAST_MS);
  }

  function send(method, url, body) {
    var opts = { method: method };
    if (body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r;
    });
  }

  function tileId(tile) {
    return Number(tile.getAttribute("data-id"));
  }

  function findTile(id) {
    return $("grid").querySelector('.tile[data-id="' + id + '"]');
  }

  function gridIds() {
    var tiles = $("grid").querySelectorAll(".tile");
    var ids = [];
    for (var i = 0; i < tiles.length; i++) ids.push(tileId(tiles[i]));
    return ids;
  }

  function applyOrder(ids) {
    var grid = $("grid");
    ids.forEach(function (id) {
      var tile = findTile(id);
      if (tile) grid.appendChild(tile);
    });
  }

  function onToggle(tile) {
    var next = tile.classList.contains("disabled");
    setToggle(tile, next);
    return send("PATCH", "/api/photos/" + tileId(tile), { enabled: next }).catch(function () {
      setToggle(tile, !next);
      showError("Could not " + (next ? "show" : "hide") + " the photo. Try again.");
    });
  }

  var drag = null;

  function startDrag(tile) {
    drag = { tile: tile, start: gridIds() };
    tile.classList.add("dragging");
  }

  function dragOver(target) {
    if (!drag || !target || target === drag.tile) return;
    var ids = gridIds();
    var from = ids.indexOf(tileId(drag.tile));
    var to = ids.indexOf(tileId(target));
    if (from === -1 || to === -1) return;
    applyOrder(moveId(ids, from, to));
  }

  function endDrag() {
    if (!drag) return;
    var d = drag;
    drag = null;
    d.tile.classList.remove("dragging");
    var ids = gridIds();
    if (ids.join(",") === d.start.join(",")) return;
    send("PUT", "/api/photos/order", { ids: ids }).catch(function () {
      applyOrder(d.start);
      showError("Could not save the new order. Try again.");
    });
  }

  function onPointerDown(e) {
    var handle = e.target.closest(".tile-handle");
    if (!handle || e.pointerType === "mouse" || drag) return;
    e.preventDefault();
    startDrag(handle.closest(".tile"));
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    if (!drag) return;
    e.preventDefault();
    var el = document.elementFromPoint(e.clientX, e.clientY);
    dragOver(el && el.closest("#grid .tile"));
  }

  function onPointerUp() {
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);
    endDrag();
  }

  function onDragStart(e) {
    var handle = e.target.closest && e.target.closest(".tile-handle");
    if (!handle) return;
    if (drag) {
      e.preventDefault();
      return;
    }
    var tile = handle.closest(".tile");
    startDrag(tile);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(tileId(tile)));
    if (e.dataTransfer.setDragImage) {
      e.dataTransfer.setDragImage(tile, tile.offsetWidth / 2, tile.offsetHeight / 2);
    }
  }

  function onDragOver(e) {
    if (!drag) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    dragOver(e.target.closest(".tile"));
  }

  function onDrop(e) {
    if (!drag) return;
    e.preventDefault();
    endDrag();
  }

  var sheetTile = null;
  var sheetOpener = null;

  function showSheetPart(name) {
    $("sheet-actions").hidden = name !== "actions";
    $("sheet-caption").hidden = name !== "caption";
  }

  function openSheet(tile, opener) {
    sheetTile = tile;
    sheetOpener = opener || null;
    showSheetPart("actions");
    $("sheet").hidden = false;
    $("sheet-edit").focus();
  }

  function closeSheet() {
    $("sheet").hidden = true;
    var opener = sheetOpener;
    sheetTile = null;
    sheetOpener = null;
    if (opener && document.body.contains(opener)) opener.focus();
  }

  function openCaption() {
    if (!sheetTile) return;
    var input = $("caption-input");
    input.value = sheetTile.getAttribute("data-caption") || "";
    showSheetPart("caption");
    input.focus();
    input.select();
  }

  function saveCaption() {
    var tile = sheetTile;
    if (!tile) return;
    var text = $("caption-input").value.trim();
    var caption = text === "" ? null : text;
    var btn = $("caption-save");
    btn.disabled = true;
    return send("PATCH", "/api/photos/" + tileId(tile), { caption: caption }).then(function () {
      tile.setAttribute("data-caption", caption || "");
      tile.querySelector("img").alt = caption || "";
      closeSheet();
    }).catch(function () {
      showError("Could not save the caption. Try again.");
    }).then(function () {
      btn.disabled = false;
    });
  }

  function deletePhoto() {
    var tile = sheetTile;
    if (!tile) return;
    if (!window.confirm("Delete this photo? It is removed from the board and the Pi.")) return;
    closeSheet();
    return send("DELETE", "/api/photos/" + tileId(tile)).then(function () {
      if (tile.parentNode) tile.parentNode.removeChild(tile);
      var n = $("grid").querySelectorAll(".tile").length;
      $("grid-empty").hidden = n > 0;
      setCount(n);
    }).catch(function () {
      showError("Could not delete the photo. Try again.");
    });
  }

  function onGridClick(e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    var tile = btn.closest(".tile");
    if (!tile) return;
    if (btn.classList.contains("tile-toggle")) onToggle(tile);
    else if (btn.classList.contains("tile-menu")) openSheet(tile, btn);
  }

  function initTiles() {
    var grid = $("grid");
    grid.addEventListener("click", onGridClick);
    grid.addEventListener("pointerdown", onPointerDown);
    grid.addEventListener("dragstart", onDragStart);
    grid.addEventListener("dragover", onDragOver);
    grid.addEventListener("drop", onDrop);
    grid.addEventListener("dragend", endDrag);

    $("sheet-backdrop").addEventListener("click", closeSheet);
    $("sheet-cancel").addEventListener("click", closeSheet);
    $("sheet-edit").addEventListener("click", openCaption);
    $("sheet-delete").addEventListener("click", deletePhoto);
    $("caption-cancel").addEventListener("click", closeSheet);
    $("caption-save").addEventListener("click", saveCaption);
    $("caption-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        saveCaption();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("sheet").hidden) closeSheet();
    });
  }

  function addRow(file) {
    var li = document.createElement("li");
    var top = document.createElement("div");
    top.className = "name";
    var name = document.createElement("span");
    name.textContent = file.name;
    var state = document.createElement("span");
    top.appendChild(name);
    top.appendChild(state);
    var bar = document.createElement("progress");
    bar.max = 100;
    bar.value = 0;
    li.appendChild(top);
    li.appendChild(bar);
    $("upload-list").appendChild(li);
    return {
      progress: function (pct) {
        bar.value = pct;
        state.textContent = pct + "%";
      },
      done: function () {
        bar.value = 100;
        bar.hidden = true;
        state.textContent = "Done";
        state.className = "done";
      },
      fail: function (msg) {
        bar.hidden = true;
        state.textContent = msg;
        state.className = "fail";
      }
    };
  }

  function uploadOne(file, row) {
    return new Promise(function (resolve) {
      var form = new FormData();
      form.append("files", file, file.name);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/photos");
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) row.progress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) row.done();
        else row.fail(errorFromResponse(xhr));
        resolve();
      };
      xhr.onerror = function () {
        row.fail("Network error, the upload did not finish");
        resolve();
      };
      xhr.send(form);
    });
  }

  function onFiles(e) {
    var files = Array.prototype.slice.call(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    $("upload-list").textContent = "";
    var jobs = files.map(function (file) {
      var row = addRow(file);
      if (!isSupported(file)) {
        row.fail(TYPE_ERROR);
        return Promise.resolve();
      }
      return uploadOne(file, row);
    });
    Promise.all(jobs).then(function () {
      loadGrid().catch(function () {});
      loadStatus();
    });
  }

  var current = {};
  var intervalTimer = null;

  function showSettingsError(msg) {
    var el = $("settings-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function renderSettings(s) {
    current = s;
    $("interval").value = s.interval_s;
    var segs = document.querySelectorAll(".seg");
    for (var i = 0; i < segs.length; i++) {
      var key = segs[i].getAttribute("data-key");
      var buttons = segs[i].querySelectorAll("button");
      for (var j = 0; j < buttons.length; j++) {
        var active = buttons[j].getAttribute("data-value") === String(s[key]);
        buttons[j].classList.toggle("active", active);
        buttons[j].setAttribute("aria-pressed", active ? "true" : "false");
      }
    }
  }

  function loadSettings() {
    return getJSON("/api/settings").then(renderSettings);
  }

  function putSettings(patch) {
    return fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Could not save");
        showSettingsError("");
        renderSettings(body);
      });
    }).catch(function (err) {
      showSettingsError(plainError(err.message) + ". Try again.");
      loadSettings().catch(function () {});
    });
  }

  function clampInterval(v) {
    v = Math.round(Number(v) / 5) * 5;
    if (!isFinite(v)) v = current.interval_s || 20;
    return Math.min(600, Math.max(5, v));
  }

  function queueInterval(v) {
    $("interval").value = v;
    clearTimeout(intervalTimer);
    intervalTimer = setTimeout(function () {
      putSettings({ interval_s: v });
    }, INTERVAL_DEBOUNCE_MS);
  }

  function stepInterval(delta) {
    queueInterval(clampInterval(Number($("interval").value) + delta));
  }

  function onIntervalInput() {
    var raw = $("interval").value;
    if (raw === "") return;
    var v = clampInterval(raw);
    clearTimeout(intervalTimer);
    intervalTimer = setTimeout(function () {
      $("interval").value = v;
      putSettings({ interval_s: v });
    }, INTERVAL_DEBOUNCE_MS);
  }

  function onSegClick(e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    var key = e.currentTarget.getAttribute("data-key");
    var raw = btn.getAttribute("data-value");
    var value = raw === "true" ? true : raw === "false" ? false : raw;
    var patch = {};
    patch[key] = value;
    var next = {};
    for (var k in current) next[k] = current[k];
    next[key] = value;
    renderSettings(next);
    putSettings(patch);
  }

  function init() {
    $("file-input").addEventListener("change", onFiles);
    $("interval-minus").addEventListener("click", function () { stepInterval(-5); });
    $("interval-plus").addEventListener("click", function () { stepInterval(5); });
    $("interval").addEventListener("input", onIntervalInput);
    $("interval").addEventListener("change", function () {
      if ($("interval").value === "") $("interval").value = current.interval_s;
    });
    var segs = document.querySelectorAll(".seg");
    for (var i = 0; i < segs.length; i++) segs[i].addEventListener("click", onSegClick);
    initTiles();

    loadSettings().catch(function () {
      showSettingsError("Could not load settings");
    });
    loadGrid().catch(function () {});
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
  }

  init();
})(typeof window !== "undefined" ? window : globalThis);
