(function (root) {
  "use strict";

  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function isoOf(d) {
    return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate());
  }

  function parseIso(iso) {
    var p = iso.split("-");
    return new Date(Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2])));
  }

  function addDays(iso, n) {
    return isoOf(new Date(parseIso(iso).getTime() + n * 86400000));
  }

  function gridDays(year, month) {
    var first = new Date(Date.UTC(year, month - 1, 1));
    var last = new Date(Date.UTC(year, month, 0));
    var start = Date.UTC(year, month - 1, 1 - first.getUTCDay());
    var count = first.getUTCDay() + last.getUTCDate() + (6 - last.getUTCDay());
    var cells = [];
    for (var i = 0; i < count; i++) {
      var d = new Date(start + i * 86400000);
      cells.push({ date: isoOf(d), day: d.getUTCDate(), other: d.getUTCMonth() !== month - 1 });
    }
    return cells;
  }

  function formatTime(hhmm) {
    if (!hhmm) return "All day";
    var p = hhmm.split(":");
    var h = Number(p[0]);
    return (h % 12 || 12) + ":" + p[1] + (h < 12 ? " am" : " pm");
  }

  function dayTitle(iso) {
    var d = parseIso(iso);
    return WEEKDAYS[d.getUTCDay()] + ", " + MONTHS[d.getUTCMonth()] + " " + d.getUTCDate();
  }

  function shiftMonth(year, month, delta) {
    var m = year * 12 + (month - 1) + delta;
    return { year: Math.floor(m / 12), month: (m % 12) + 1 };
  }

  function clampDay(year, month, day) {
    var max = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return year + "-" + pad(month) + "-" + pad(Math.min(day, max));
  }

  function countLabel(n, tail) {
    return n + (n === 1 ? " event" : " events") + (tail || "");
  }

  function localToday(now) {
    return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
  }

  var Calendar = {
    MONTHS: MONTHS,
    WEEKDAYS: WEEKDAYS,
    pad: pad,
    parseIso: parseIso,
    addDays: addDays,
    gridDays: gridDays,
    formatTime: formatTime,
    dayTitle: dayTitle,
    shiftMonth: shiftMonth,
    clampDay: clampDay,
    countLabel: countLabel,
    localToday: localToday
  };
  root.Calendar = Calendar;
  if (typeof module !== "undefined" && module.exports) module.exports = Calendar;
})(typeof window !== "undefined" ? window : globalThis);
