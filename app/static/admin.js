(function (root) {
  "use strict";

  function moveId(ids, from, to) {
    var out = ids.slice();
    if (from < 0 || from >= out.length || to < 0 || to >= out.length) return out;
    var item = out.splice(from, 1)[0];
    out.splice(to, 0, item);
    return out;
  }

  var TYPES = ["image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/heic", "image/heif", "image/webp"];
  var EXTS = ["jpg", "jpeg", "png", "heic", "heif", "webp"];

  function isSupported(file) {
    var type = (file.type || "").toLowerCase();
    var ext = ((file.name || "").split(".").pop() || "").toLowerCase();
    if (TYPES.indexOf(type) !== -1 || EXTS.indexOf(ext) !== -1) return true;
    return type === "" || type === "application/octet-stream" || type.indexOf("image/") === 0;
  }

  function photoPatch(photo, captionText, enabled) {
    var text = (captionText || "").trim();
    var caption = text === "" ? null : text;
    var patch = {};
    if (caption !== (photo.caption || null)) patch.caption = caption;
    if (enabled !== !!photo.enabled) patch.enabled = enabled;
    return patch;
  }

  function svgIcon(d) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 12 12");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
    return svg;
  }

  var ICON_EYE_OFF = "M1 6C3 3 9 3 11 6C9 9 3 9 1 6ZM5 6a1 1 0 1 0 2 0a1 1 0 1 0-2 0M1.5 10.5L10.5 1.5";

  function makeBadge(cls) {
    var badge = document.createElement("span");
    badge.className = "badge " + cls;
    badge.setAttribute("aria-hidden", "true");
    return badge;
  }

  function setBadge(tile, cls, on, fill) {
    var badge = tile.querySelector("." + cls);
    if (!on) {
      if (badge) tile.removeChild(badge);
      return;
    }
    if (badge) return;
    badge = makeBadge(cls);
    fill(badge);
    tile.insertBefore(badge, tile.querySelector(".tile-handle"));
  }

  function tileLabel(p) {
    return "Photo" + (p.caption ? ": " + p.caption : "") + (p.enabled ? "" : ", hidden from the board");
  }

  function setTileState(tile, p) {
    tile.classList.toggle("disabled", !p.enabled);
    tile.setAttribute("data-caption", p.caption || "");
    var open = tile.querySelector(".tile-open");
    open.setAttribute("aria-label", tileLabel(p));
    open.querySelector("img").alt = p.caption || "";
    setBadge(tile, "badge-hidden", !p.enabled, function (b) {
      b.appendChild(svgIcon(ICON_EYE_OFF));
    });
    setBadge(tile, "badge-cc", !!p.caption, function (b) {
      b.textContent = "CC";
    });
  }

  function buildTile(p) {
    var tile = document.createElement("div");
    tile.className = "tile";
    tile.setAttribute("data-id", p.id);
    var open = document.createElement("button");
    open.type = "button";
    open.className = "tile-open";
    open.setAttribute("aria-haspopup", "dialog");
    var img = document.createElement("img");
    img.src = p.thumb_url;
    img.loading = "lazy";
    img.decoding = "async";
    img.draggable = false;
    open.appendChild(img);
    tile.appendChild(open);

    var handle = document.createElement("span");
    handle.className = "tile-handle";
    handle.draggable = true;
    handle.title = "Drag to reorder";
    handle.setAttribute("aria-hidden", "true");
    var grip = makeBadge("badge-handle");
    grip.textContent = String.fromCharCode(0x2261);
    handle.appendChild(grip);
    tile.appendChild(handle);
    setTileState(tile, p);
    return tile;
  }

  function runLimited(items, limit, worker) {
    return new Promise(function (resolve) {
      var next = 0;
      var active = 0;
      function finish() {
        active--;
        pump();
      }
      function pump() {
        if (next >= items.length && active === 0) {
          resolve();
          return;
        }
        while (active < limit && next < items.length) {
          var item = items[next++];
          active++;
          var job;
          try {
            job = Promise.resolve(worker(item));
          } catch (err) {
            job = Promise.resolve();
          }
          job.then(finish, finish);
        }
      }
      pump();
    });
  }

  var Admin = { moveId: moveId, runLimited: runLimited, isSupported: isSupported, photoPatch: photoPatch, buildTile: buildTile, setTileState: setTileState };
  root.Admin = Admin;
  if (typeof module !== "undefined" && module.exports) module.exports = Admin;
  if (typeof document === "undefined") return;

  var TYPE_ERROR = "Only JPEG, PNG, HEIC and WebP photos";
  var UPLOAD_LIMIT = 2;
  var SERVER_ERRORS = {
    "too large": "This photo is too large",
    "cannot open": "This file could not be opened as a photo",
    "unsupported format": TYPE_ERROR,
    "processing failed": "The photo could not be processed",
    "save failed": "The photo could not be saved"
  };
  var STATUS_MS = 10000;
  var FIRST_PAGE = 12;
  var PAGE = 24;
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
      setDisplayState(s.display_connected ? "Display online" : "Display offline");
      setCount(s.photo_count);
    }).catch(function () {
      setDisplayState("Server not reachable");
    });
  }

  function setDisplayState(text) {
    $("display-state").textContent = text;
  }

  var photos = [];
  var shown = FIRST_PAGE;

  function findPhoto(id) {
    for (var i = 0; i < photos.length; i++) if (photos[i].id === id) return photos[i];
    return null;
  }

  function loadGrid() {
    return getJSON("/api/photos?all=1").then(function (list) {
      photos = list;
      renderGrid();
    });
  }

  function renderGrid() {
    var grid = $("grid");
    grid.textContent = "";
    photos.slice(0, shown).forEach(function (p) {
      grid.appendChild(buildTile(p));
    });
    updateGridMeta();
  }

  function updateGridMeta() {
    var rendered = $("grid").querySelectorAll(".tile").length;
    var left = photos.length - rendered;
    var more = $("show-more");
    more.hidden = left <= 0;
    more.textContent = "Show More (" + left + ")";
    $("grid-empty").hidden = photos.length > 0;
    setCount(photos.length);
  }

  function showMore() {
    var grid = $("grid");
    var from = grid.querySelectorAll(".tile").length;
    shown = from + PAGE;
    photos.slice(from, shown).forEach(function (p) {
      grid.appendChild(buildTile(p));
    });
    updateGridMeta();
  }

  var toastTimer = null;

  function showError(msg) {
    var el = $("toast");
    clearTimeout(toastTimer);
    $("toast-text").textContent = msg;
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
    var head = ids.map(findPhoto).filter(Boolean);
    photos = head.concat(photos.filter(function (p) { return ids.indexOf(p.id) === -1; }));
  }

  function fullOrder(ids) {
    return ids.concat(photos.map(function (p) { return p.id; }).filter(function (id) {
      return ids.indexOf(id) === -1;
    }));
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
    var ids = fullOrder(gridIds());
    if (ids.join(",") === fullOrder(d.start).join(",")) return;
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

  var dialogTile = null;

  function showDialogPart(name) {
    $("photo-edit").hidden = name !== "edit";
    $("photo-confirm").hidden = name !== "confirm";
  }

  function openDialog(tile) {
    var p = findPhoto(tileId(tile));
    if (!p) return;
    dialogTile = tile;
    $("photo-thumb").src = tile.querySelector("img").src;
    $("caption-input").value = p.caption || "";
    $("photo-enabled").checked = !!p.enabled;
    showDialogPart("edit");
    $("photo-dialog").hidden = false;
    $("caption-input").focus();
  }

  function openConfirm() {
    if (!dialogTile) return;
    showDialogPart("confirm");
    $("confirm-delete").focus();
  }

  function backToEdit() {
    showDialogPart("edit");
    $("photo-delete").focus();
  }

  function closeDialog() {
    $("photo-dialog").hidden = true;
    var tile = dialogTile;
    dialogTile = null;
    if (tile && document.body.contains(tile)) tile.querySelector(".tile-open").focus();
  }

  function savePhoto() {
    var tile = dialogTile;
    if (!tile) return;
    var p = findPhoto(tileId(tile));
    if (!p) return;
    var patch = photoPatch(p, $("caption-input").value, $("photo-enabled").checked);
    if (!Object.keys(patch).length) {
      closeDialog();
      return;
    }
    var btn = $("photo-save");
    btn.disabled = true;
    return send("PATCH", "/api/photos/" + tileId(tile), patch).then(function () {
      for (var k in patch) p[k] = patch[k];
      setTileState(tile, p);
      if (dialogTile === tile) closeDialog();
    }).catch(function () {
      showError("Could not save the photo. Try again.");
    }).then(function () {
      btn.disabled = false;
    });
  }

  function deletePhoto() {
    var tile = dialogTile;
    if (!tile) return;
    closeDialog();
    var id = tileId(tile);
    return send("DELETE", "/api/photos/" + id).then(function () {
      if (tile.parentNode) tile.parentNode.removeChild(tile);
      photos = photos.filter(function (p) { return p.id !== id; });
      var grid = $("grid");
      var next = photos[grid.querySelectorAll(".tile").length];
      if (next && grid.querySelectorAll(".tile").length < shown) grid.appendChild(buildTile(next));
      updateGridMeta();
    }).catch(function () {
      showError("Could not delete the photo. Try again.");
    });
  }

  function onGridClick(e) {
    var btn = e.target.closest(".tile-open");
    if (!btn) return;
    openDialog(btn.closest(".tile"));
  }

  function initTiles() {
    var grid = $("grid");
    grid.addEventListener("click", onGridClick);
    grid.addEventListener("pointerdown", onPointerDown);
    grid.addEventListener("dragstart", onDragStart);
    grid.addEventListener("dragover", onDragOver);
    grid.addEventListener("drop", onDrop);
    grid.addEventListener("dragend", endDrag);

    $("photo-backdrop").addEventListener("click", closeDialog);
    $("photo-close").addEventListener("click", closeDialog);
    $("photo-cancel").addEventListener("click", closeDialog);
    $("photo-save").addEventListener("click", savePhoto);
    $("photo-delete").addEventListener("click", openConfirm);
    $("confirm-delete").addEventListener("click", deletePhoto);
    $("confirm-cancel").addEventListener("click", backToEdit);
    $("show-more").addEventListener("click", showMore);
    $("caption-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        savePhoto();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("photo-dialog").hidden) closeDialog();
      else if (!$("upload-dialog").hidden && !$("upload-close").disabled) closeUpload();
    });
  }

  function addRow(file) {
    var li = document.createElement("li");
    var name = document.createElement("div");
    name.className = "name";
    name.textContent = file.name;
    var frame = document.createElement("div");
    frame.className = "w95-progress";
    var bar = document.createElement("div");
    bar.className = "w95-progress-bar";
    frame.appendChild(bar);
    var state = document.createElement("div");
    state.className = "state";
    state.textContent = "Waiting...";
    li.appendChild(name);
    li.appendChild(frame);
    li.appendChild(state);
    $("upload-list").appendChild(li);
    return {
      progress: function (pct) {
        bar.style.width = pct + "%";
        state.textContent = pct + "%";
      },
      done: function () {
        bar.style.width = "100%";
        state.textContent = "Done";
        state.className = "state done";
      },
      fail: function (msg) {
        frame.hidden = true;
        state.textContent = msg;
        state.className = "state fail";
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

  function closeUpload() {
    $("upload-dialog").hidden = true;
    $("file-input").focus();
  }

  function onFiles(e) {
    var files = Array.prototype.slice.call(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    $("upload-list").textContent = "";
    $("upload-close").disabled = true;
    $("upload-dialog").hidden = false;
    var jobs = [];
    files.forEach(function (file) {
      var row = addRow(file);
      if (!isSupported(file)) row.fail(TYPE_ERROR);
      else jobs.push({ file: file, row: row });
    });
    runLimited(jobs, UPLOAD_LIMIT, function (job) {
      return uploadOne(job.file, job.row);
    }).then(function () {
      $("upload-close").disabled = false;
      $("upload-close").focus();
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
    var radios = document.querySelectorAll('input[name="fit"]');
    for (var i = 0; i < radios.length; i++) radios[i].checked = radios[i].value === String(s.fit);
    var checks = document.querySelectorAll(".checks input[data-key]");
    for (var n = 0; n < checks.length; n++) checks[n].checked = !!s[checks[n].getAttribute("data-key")];
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
    v = Math.round(Number(v));
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

  function holdStep(btn, delta) {
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
      stepInterval(delta);
      delay = setTimeout(function () {
        repeat = setInterval(function () { stepInterval(delta); }, 80);
      }, 400);
    });
    btn.addEventListener("pointerup", stop);
    btn.addEventListener("pointerleave", stop);
    btn.addEventListener("pointercancel", stop);
    btn.addEventListener("click", function (e) {
      if (e.detail === 0) stepInterval(delta);
    });
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

  function onCheckChange(e) {
    setSetting(e.currentTarget.getAttribute("data-key"), e.currentTarget.checked);
  }

  function onFitChange(e) {
    if (e.currentTarget.checked) setSetting("fit", e.currentTarget.value);
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

  function init() {
    $("file-input").addEventListener("change", onFiles);
    $("upload-close").addEventListener("click", closeUpload);
    holdStep($("interval-minus"), -1);
    holdStep($("interval-plus"), 1);
    $("interval").addEventListener("input", onIntervalInput);
    $("interval").addEventListener("change", function () {
      if ($("interval").value === "") $("interval").value = current.interval_s;
    });
    var radios = document.querySelectorAll('input[name="fit"]');
    for (var i = 0; i < radios.length; i++) radios[i].addEventListener("change", onFitChange);
    var checks = document.querySelectorAll(".checks input[data-key]");
    for (var j = 0; j < checks.length; j++) checks[j].addEventListener("change", onCheckChange);
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
