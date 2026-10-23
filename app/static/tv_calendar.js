(function (root) {
  "use strict";

  var C = root.Calendar || require("./calendar_util.js");
  var UPCOMING_DAYS = 7;
  var MAX_UPCOMING = 10;
  var MAX_BACKOFF_S = 30;
  var SHORT_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function shortTime(hhmm) {
    var p = hhmm.split(":");
    var h = Number(p[0]);
    if (p[1] !== "00") return (h % 12 || 12) + ":" + p[1];
    return (h % 12 || 12) + (h < 12 ? "am" : "pm");
  }

  function chipText(occ) {
    return occ.time ? occ.title + " " + shortTime(occ.time) : occ.title;
  }

  function whenLabel(occ, today) {
    var day;
    if (occ.date === today) day = "Today";
    else if (occ.date === C.addDays(today, 1)) day = "Tomorrow";
    else {
      var d = C.parseIso(occ.date);
      return SHORT_DAYS[d.getUTCDay()] + " " + SHORT_MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() +
        (occ.time ? ", " + C.formatTime(occ.time) : "");
    }
    return day + ", " + (occ.time ? C.formatTime(occ.time) : "all day");
  }

  function upcoming(occurrences, today) {
    var last = C.addDays(today, UPCOMING_DAYS - 1);
    return occurrences.filter(function (o) {
      return o.date >= today && o.date <= last;
    }).map(function (o, i) {
      return { o: o, i: i };
    }).sort(function (a, b) {
      var x = a.o;
      var y = b.o;
      if (x.date !== y.date) return x.date < y.date ? -1 : 1;
      if (!x.time !== !y.time) return x.time ? 1 : -1;
      if ((x.time || "") !== (y.time || "")) return x.time < y.time ? -1 : 1;
      return a.i - b.i;
    }).slice(0, MAX_UPCOMING).map(function (x) {
      return { title: x.o.title, when: whenLabel(x.o, today) };
    });
  }

  function fetchRange(cells, today) {
    var end = cells[cells.length - 1].date;
    var last = C.addDays(today, UPCOMING_DAYS - 1);
    return { start: cells[0].date, end: last > end ? last : end };
  }

  function clockText(now) {
    return C.formatTime(C.pad(now.getHours()) + ":" + C.pad(now.getMinutes()));
  }

  function msToMidnight(now) {
    var next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 5);
    return next.getTime() - now.getTime();
  }

  var TvCalendar = {
    MAX_UPCOMING: MAX_UPCOMING,
    shortTime: shortTime,
    chipText: chipText,
    whenLabel: whenLabel,
    upcoming: upcoming,
    fetchRange: fetchRange,
    clockText: clockText,
    msToMidnight: msToMidnight
  };

  root.TvCalendar = TvCalendar;
  if (typeof module !== "undefined" && module.exports) module.exports = TvCalendar;
  if (typeof document === "undefined") return;

  var gridEl = document.getElementById("cal-grid");
  var upEl = document.getElementById("upcoming");
  var titleEl = document.getElementById("month-title");
  var clockEl = document.getElementById("clock");
  var countEl = document.getElementById("month-count");
  var todayEl = document.getElementById("today");
  var today;
  var year;
  var month;
  var cells = [];
  var occurrences = [];
  var loadSeq = 0;
  var backoff = 1;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setToday() {
    today = C.localToday(new Date());
    year = Number(today.slice(0, 4));
    month = Number(today.slice(5, 7));
    cells = C.gridDays(year, month);
    titleEl.textContent = C.MONTHS[month - 1] + " " + year;
    todayEl.textContent = C.dayTitle(today);
  }

  function fitChips() {
    var lists = gridEl.querySelectorAll(".chips");
    for (var i = 0; i < lists.length; i++) {
      var list = lists[i];
      var chips = list.querySelectorAll(".ev");
      var more = null;
      var n = chips.length;
      while (n > 0 && list.scrollHeight > list.clientHeight) {
        n--;
        list.removeChild(chips[n]);
        if (!more) {
          more = el("i", "ev more");
          list.appendChild(more);
        }
        more.textContent = "+" + (chips.length - n);
      }
    }
  }

  function render() {
    var byDay = {};
    var inMonth = 0;
    var prefix = year + "-" + C.pad(month) + "-";
    occurrences.forEach(function (o) {
      (byDay[o.date] = byDay[o.date] || []).push(o);
      if (o.date.indexOf(prefix) === 0) inMonth++;
    });
    gridEl.textContent = "";
    SHORT_DAYS.forEach(function (name) {
      gridEl.appendChild(el("span", "h", name));
    });
    cells.forEach(function (c) {
      var cell = el("div", "d" + (c.other ? " other" : "") + (c.date === today ? " today" : ""));
      cell.setAttribute("data-date", c.date);
      cell.appendChild(el("b", "", String(c.day)));
      var list = el("div", "chips");
      (byDay[c.date] || []).forEach(function (o) {
        list.appendChild(el("i", "ev", chipText(o)));
      });
      cell.appendChild(list);
      gridEl.appendChild(cell);
    });
    fitChips();
    countEl.textContent = C.countLabel(inMonth, " this month");
    upEl.textContent = "";
    var next = upcoming(occurrences, today);
    if (!next.length) upEl.appendChild(el("li", "none", "Nothing coming up"));
    next.forEach(function (u) {
      var li = el("li", "", u.title);
      li.appendChild(el("small", "", u.when));
      upEl.appendChild(li);
    });
  }

  function load() {
    var seq = ++loadSeq;
    var range = fetchRange(cells, today);
    return fetch("/api/events?start=" + range.start + "&end=" + range.end, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (data) {
      if (seq !== loadSeq) return;
      occurrences = data;
      render();
    }, function () {});
  }

  function connect() {
    var es;
    try {
      es = new EventSource("/api/events");
    } catch (e) {
      setTimeout(connect, backoff * 1000);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_S);
      return;
    }
    es.onopen = function () { backoff = 1; };
    es.addEventListener("hello", load);
    es.addEventListener("events_changed", load);
    es.onerror = function () {
      es.close();
      setTimeout(connect, backoff * 1000);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_S);
    };
  }

  function updateClock() {
    var now = new Date();
    clockEl.textContent = clockText(now);
    setTimeout(updateClock, 60000 - now.getSeconds() * 1000 - now.getMilliseconds() + 20);
  }

  function atMidnight() {
    setToday();
    render();
    load();
    setTimeout(atMidnight, msToMidnight(new Date()));
  }

  setToday();
  render();
  updateClock();
  setTimeout(atMidnight, msToMidnight(new Date()));
  window.addEventListener("resize", render);
  load().then(connect);
})(typeof window !== "undefined" ? window : globalThis);
