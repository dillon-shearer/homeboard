(function () {
  "use strict";

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
        var tile = document.createElement("div");
        tile.className = "tile" + (p.enabled ? "" : " disabled");
        tile.setAttribute("data-id", p.id);
        var img = document.createElement("img");
        img.src = p.thumb_url;
        img.alt = p.caption || "";
        img.loading = "lazy";
        tile.appendChild(img);
        grid.appendChild(tile);
      });
      $("grid-empty").hidden = photos.length > 0;
      setCount(photos.length);
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

    loadSettings().catch(function () {
      showSettingsError("Could not load settings");
    });
    loadGrid().catch(function () {});
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
  }

  init();
})();
