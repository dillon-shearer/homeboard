(function (root) {
  "use strict";

  var Calendar = root.Calendar || require("./calendar_util.js");
  var pad = Calendar.pad;
  var gridDays = Calendar.gridDays;
  var formatTime = Calendar.formatTime;
  var dayTitle = Calendar.dayTitle;
  var shiftMonth = Calendar.shiftMonth;
  var clampDay = Calendar.clampDay;
  var countLabel = Calendar.countLabel;
  var localToday = Calendar.localToday;
  root.Calendar = Calendar;
  if (typeof module !== "undefined" && module.exports) module.exports = Calendar;
  if (typeof document === "undefined") return;

  var STATUS_MS = 10000;
  var TOAST_MS = 4000;
  var TOAST_FADE_MS = 400;
  var MAX_BACKOFF_S = 30;

  function $(id) {
    return document.getElementById(id);
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
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

  var today = localToday(new Date());
  var view = { year: Number(today.slice(0, 4)), month: Number(today.slice(5, 7)) };
  var selected = today;
  var cells = [];
  var occurrences = [];
  var loadSeq = 0;
  var editing = null;

  function loadStatus() {
    return getJSON("/api/status").then(function (s) {
      $("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
    }).catch(function () {
      $("display-state").textContent = "Server not reachable";
    });
  }

  function fillYears() {
    var sel = $("year-select");
    var base = Number(today.slice(0, 4));
    var years = [];
    for (var y = base - 1; y <= base + 3; y++) years.push(y);
    if (years.indexOf(view.year) === -1) {
      years.push(view.year);
      years.sort();
    }
    sel.textContent = "";
    years.forEach(function (y) {
      var o = document.createElement("option");
      o.value = y;
      o.textContent = y;
      sel.appendChild(o);
    });
    sel.value = String(view.year);
  }

  function load() {
    var seq = ++loadSeq;
    var start = cells[0].date;
    var end = cells[cells.length - 1].date;
    return getJSON("/api/events?start=" + start + "&end=" + end).then(function (data) {
      if (seq !== loadSeq) return;
      occurrences = data;
      $("cal-error").hidden = true;
      render();
    }).catch(function () {
      if (seq !== loadSeq) return;
      $("cal-error").hidden = false;
    });
  }

  function setView(year, month, day) {
    view = { year: year, month: month };
    selected = day || clampDay(year, month, Number(selected.slice(8, 10)));
    cells = gridDays(year, month);
    occurrences = [];
    fillYears();
    $("month-select").value = String(month);
    render();
    load();
  }

  function onDay(iso) {
    return occurrences.filter(function (o) { return o.date === iso; });
  }

  function render() {
    var marked = {};
    var inMonth = 0;
    var prefix = view.year + "-" + pad(view.month) + "-";
    occurrences.forEach(function (o) {
      marked[o.date] = true;
      if (o.date.indexOf(prefix) === 0) inMonth++;
    });
    var grid = $("cal-grid");
    grid.textContent = "";
    cells.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cal-day" + (c.other ? " other" : "") + (c.date === today ? " today" : "") + (c.date === selected ? " sel" : "");
      b.setAttribute("data-date", c.date);
      b.setAttribute("aria-label", dayTitle(c.date) + (marked[c.date] ? ", has events" : ""));
      if (c.date === selected) b.setAttribute("aria-pressed", "true");
      var n = document.createElement("span");
      n.textContent = c.day;
      b.appendChild(n);
      if (marked[c.date]) {
        var dot = document.createElement("i");
        dot.className = "dot";
        b.appendChild(dot);
      }
      grid.appendChild(b);
    });
    $("month-count").textContent = countLabel(inMonth, " this month");
    renderDay();
  }

  function renderDay() {
    var list = onDay(selected);
    $("day-title").textContent = dayTitle(selected);
    $("day-count").textContent = countLabel(list.length);
    var ul = $("day-events");
    ul.textContent = "";
    list.forEach(function (o, i) {
      var li = document.createElement("li");
      var t = document.createElement("span");
      t.className = "t";
      t.textContent = formatTime(o.time);
      li.appendChild(t);
      var text = document.createElement("span");
      text.className = "text";
      text.textContent = o.title;
      li.appendChild(text);
      var edit = document.createElement("button");
      edit.type = "button";
      edit.className = "w95-btn edit";
      edit.setAttribute("data-index", i);
      edit.setAttribute("aria-label", "Edit " + o.title);
      edit.textContent = "Edit";
      li.appendChild(edit);
      ul.appendChild(li);
    });
    $("day-empty").hidden = list.length > 0;
  }

  function onGridClick(e) {
    var b = e.target.closest(".cal-day");
    if (!b) return;
    var iso = b.getAttribute("data-date");
    var y = Number(iso.slice(0, 4));
    var m = Number(iso.slice(5, 7));
    if (y !== view.year || m !== view.month) {
      setView(y, m, iso);
      return;
    }
    selected = iso;
    render();
  }

  function step(delta) {
    var v = shiftMonth(view.year, view.month, delta);
    setView(v.year, v.month);
  }

  function syncAllDay() {
    var allDay = $("event-allday").checked;
    $("event-time").disabled = allDay;
  }

  function openEvent(occ) {
    editing = occ;
    $("event-heading").textContent = occ ? "Edit Event" : "Add Event";
    $("event-title").value = occ ? occ.title : "";
    $("event-date").value = occ ? occ.date : selected;
    $("event-time").value = occ && occ.time ? occ.time : "";
    $("event-allday").checked = occ ? !occ.time : true;
    $("event-repeat").value = occ ? occ.repeat : "never";
    $("event-delete").hidden = !occ;
    syncAllDay();
    $("event-dialog").hidden = false;
    $("event-title").focus();
  }

  function closeEvent() {
    $("event-dialog").hidden = true;
    editing = null;
  }

  function onDayClick(e) {
    var b = e.target.closest(".edit");
    if (!b) return;
    var occ = onDay(selected)[Number(b.getAttribute("data-index"))];
    if (occ) openEvent(occ);
  }

  function onAllDayChange() {
    syncAllDay();
    if (!$("event-allday").checked) $("event-time").focus();
  }

  function saveEvent(e) {
    e.preventDefault();
    var title = $("event-title").value.trim();
    var day = $("event-date").value;
    var allDay = $("event-allday").checked;
    var time = allDay ? null : $("event-time").value;
    var repeat = $("event-repeat").value;
    if (!title) {
      showError("Type what the event is.");
      $("event-title").focus();
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
      showError("Pick a date.");
      $("event-date").focus();
      return;
    }
    if (!allDay && !time) {
      showError("Pick a time or tick All day.");
      $("event-time").focus();
      return;
    }
    var occ = editing;
    var req;
    if (occ) {
      var patch = { title: title, time: time, repeat: repeat };
      if (day !== occ.date || repeat === "never") patch.date = day;
      req = send("PATCH", "/api/events/" + occ.id, patch);
    } else {
      req = send("POST", "/api/events", { title: title, date: day, time: time, repeat: repeat });
    }
    $("event-save").disabled = true;
    req.then(function () {
      $("event-save").disabled = false;
      closeEvent();
      var y = Number(day.slice(0, 4));
      var m = Number(day.slice(5, 7));
      if (y !== view.year || m !== view.month) {
        setView(y, m, day);
      } else {
        selected = day;
        render();
        load();
      }
    }, function () {
      $("event-save").disabled = false;
      showError(occ ? "Could not save the event. Try again." : "Could not add the event. Try again.");
    });
  }

  function openDelete() {
    if (!editing) return;
    $("delete-text").textContent = "Delete \"" + editing.title + "\"" + (editing.repeat !== "never" ? " and all its repeats?" : "?");
    $("delete-dialog").hidden = false;
    $("confirm-delete").focus();
  }

  function closeDelete() {
    $("delete-dialog").hidden = true;
  }

  function deleteEvent() {
    var occ = editing;
    closeDelete();
    if (!occ) return;
    send("DELETE", "/api/events/" + occ.id).then(function () {
      closeEvent();
      load();
    }, function () {
      showError("Could not delete the event. Try again.");
    });
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
      if (everConnected) load();
      everConnected = true;
    });
    es.addEventListener("events_changed", load);
    es.onerror = function () {
      es.close();
      retryEvents();
    };
  }

  function shortMonths() {
    if (!window.matchMedia) return;
    var mq = window.matchMedia("(max-width: 359px)");
    var opts = $("month-select").options;
    var full = [];
    for (var i = 0; i < opts.length; i++) full.push(opts[i].textContent);
    function apply() {
      for (var j = 0; j < opts.length; j++) {
        opts[j].textContent = mq.matches ? full[j].slice(0, 3) : full[j];
      }
    }
    apply();
    if (mq.addEventListener) mq.addEventListener("change", apply);
    else if (mq.addListener) mq.addListener(apply);
  }

  function init() {
    $("month-prev").addEventListener("click", function () { step(-1); });
    $("month-next").addEventListener("click", function () { step(1); });
    $("month-today").addEventListener("click", function () {
      today = localToday(new Date());
      setView(Number(today.slice(0, 4)), Number(today.slice(5, 7)), today);
    });
    $("month-select").addEventListener("change", function (e) {
      setView(view.year, Number(e.target.value));
    });
    $("year-select").addEventListener("change", function (e) {
      setView(Number(e.target.value), view.month);
    });
    shortMonths();
    $("cal-grid").addEventListener("click", onGridClick);
    $("day-events").addEventListener("click", onDayClick);
    $("add-event").addEventListener("click", function () { openEvent(null); });
    $("event-allday").addEventListener("change", onAllDayChange);
    $("event-form").addEventListener("submit", saveEvent);
    $("event-cancel").addEventListener("click", closeEvent);
    $("event-close").addEventListener("click", closeEvent);
    $("event-backdrop").addEventListener("click", closeEvent);
    $("event-delete").addEventListener("click", openDelete);
    $("confirm-delete").addEventListener("click", deleteEvent);
    $("confirm-cancel").addEventListener("click", closeDelete);
    $("delete-close").addEventListener("click", closeDelete);
    $("delete-backdrop").addEventListener("click", closeDelete);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("delete-dialog").hidden) closeDelete();
      else if (!$("event-dialog").hidden) closeEvent();
    });

    setView(view.year, view.month, selected);
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
    connectEvents();
  }

  init();
})(typeof window !== "undefined" ? window : globalThis);
