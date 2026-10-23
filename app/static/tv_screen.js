(function (root) {
  "use strict";

  var C = root.Calendar || require("./calendar_util.js");
  var MAX_BACKOFF_S = 30;
  var UPCOMING_DAYS = 7;
  var MAX_UPCOMING = 10;
  var SHORT_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var NEEDS = { calendar: ["events"] };
  var EMPTY_TEXT = "Nothing on this screen yet. Add widgets from your phone.";

  function placement(widget) {
    return {
      gridColumn: (widget.col + 1) + " / span " + widget.w,
      gridRow: (widget.row + 1) + " / span " + widget.h,
    };
  }

  function sortedScreens(screens) {
    return (screens || []).slice().sort(function (a, b) {
      return (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id;
    });
  }

  function currentScreen(screens, screenId) {
    var sorted = sortedScreens(screens);
    for (var i = 0; i < sorted.length; i++) if (sorted[i].id === screenId) return sorted[i];
    return sorted[0] || null;
  }

  function needs(screen) {
    var out = [];
    ((screen && screen.widgets) || []).forEach(function (w) {
      (NEEDS[w.kind] || []).forEach(function (key) {
        if (out.indexOf(key) === -1) out.push(key);
      });
    });
    return out;
  }

  function isFull(widget) {
    return widget.w === 2 && widget.h === 2;
  }

  function formatClock(d) {
    var h = d.getHours() % 12 || 12;
    var m = String(d.getMinutes()).padStart(2, "0");
    return h + ":" + m + " " + (d.getHours() < 12 ? "am" : "pm");
  }

  function formatDate(d) {
    return C.WEEKDAYS[d.getDay()] + ", " + C.MONTHS[d.getMonth()] + " " + d.getDate();
  }

  function fullDate(d) {
    return C.MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }

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

  function sortOccurrences(list) {
    return list.map(function (o, i) {
      return { o: o, i: i };
    }).sort(function (a, b) {
      var x = a.o;
      var y = b.o;
      if (x.date !== y.date) return x.date < y.date ? -1 : 1;
      if (!x.time !== !y.time) return x.time ? 1 : -1;
      if ((x.time || "") !== (y.time || "")) return x.time < y.time ? -1 : 1;
      return a.i - b.i;
    }).map(function (x) {
      return x.o;
    });
  }

  function between(occurrences, first, last) {
    return sortOccurrences((occurrences || []).filter(function (o) {
      return o.date >= first && o.date <= last;
    }));
  }

  function upcoming(occurrences, today) {
    return between(occurrences, today, C.addDays(today, UPCOMING_DAYS - 1)).slice(0, MAX_UPCOMING).map(function (o) {
      return { title: o.title, when: whenLabel(o, today) };
    });
  }

  function todayEvents(occurrences, today) {
    return between(occurrences, today, today).map(function (o) {
      return { title: o.title, time: o.time ? C.formatTime(o.time) : "all day" };
    });
  }

  function comingUp(occurrences, today) {
    return between(occurrences, C.addDays(today, 1), C.addDays(today, UPCOMING_DAYS)).map(function (o) {
      return { title: o.title, when: whenLabel(o, today) };
    });
  }

  function eventRange(today) {
    var cells = C.gridDays(Number(today.slice(0, 4)), Number(today.slice(5, 7)));
    var end = cells[cells.length - 1].date;
    var last = C.addDays(today, UPCOMING_DAYS);
    return { start: cells[0].date, end: last > end ? last : end };
  }

  function msToMidnight(now) {
    var next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 5);
    return next.getTime() - now.getTime();
  }

  function screenLabel(screens, screen) {
    var sorted = sortedScreens(screens);
    if (!screen) return "";
    for (var i = 0; i < sorted.length; i++) {
      if (sorted[i].id === screen.id) return "Screen " + (i + 1) + " of " + sorted.length;
    }
    return "";
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function message(card, text) {
    var old = card.querySelector(".tv-msg");
    if (old) old.textContent = text;
    else card.appendChild(el("p", "tv-msg", text));
  }

  function placeholder(name) {
    return {
      mount: function (card) { message(card, "Coming soon."); },
      update: function () {},
      title: function () { return name; },
    };
  }

  function fitChips(grid) {
    var lists = grid.querySelectorAll(".chips");
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

  function fitDay(box) {
    var lists = box.querySelectorAll("ul");
    for (var k = lists.length - 1; k >= 0; k--) {
      var ul = lists[k];
      var rows = Array.prototype.slice.call(ul.querySelectorAll("li.ev-row"));
      var more = null;
      for (var i = rows.length - 1; i >= 0 && box.scrollHeight > box.clientHeight; i--) {
        rows[i].hidden = true;
        if (!more) more = ul.appendChild(el("li", "more"));
        more.textContent = "+" + (rows.length - i) + " more";
      }
    }
  }

  function renderMonth(card, ctx) {
    var today = ctx.today;
    var year = Number(today.slice(0, 4));
    var month = Number(today.slice(5, 7));
    var occurrences = ctx.events || [];
    var byDay = {};
    occurrences.forEach(function (o) {
      (byDay[o.date] = byDay[o.date] || []).push(o);
    });
    var wrap = el("div", "tv-cal");
    var grid = el("div", "tv-cal-grid");
    SHORT_DAYS.forEach(function (name) {
      grid.appendChild(el("span", "h", name));
    });
    C.gridDays(year, month).forEach(function (c) {
      var cell = el("div", "d" + (c.other ? " other" : "") + (c.date === today ? " today" : ""));
      cell.setAttribute("data-date", c.date);
      cell.appendChild(el("b", "", String(c.day)));
      var list = el("div", "chips");
      (byDay[c.date] || []).forEach(function (o) {
        list.appendChild(el("i", "ev", chipText(o)));
      });
      cell.appendChild(list);
      grid.appendChild(cell);
    });
    var up = el("section", "tv-up");
    up.appendChild(el("h3", "", "Coming up"));
    var ul = el("ul");
    var next = upcoming(occurrences, today);
    if (!next.length) ul.appendChild(el("li", "none", "Nothing coming up"));
    next.forEach(function (u) {
      var li = el("li", "", u.title);
      li.appendChild(el("small", "", u.when));
      ul.appendChild(li);
    });
    up.appendChild(ul);
    wrap.appendChild(grid);
    wrap.appendChild(up);
    card.appendChild(wrap);
    fitChips(grid);
  }

  function renderDay(card, ctx) {
    var box = el("div", "tv-cal-day");
    var todayUl = el("ul", "today-list");
    var now = todayEvents(ctx.events, ctx.today);
    if (!now.length) todayUl.appendChild(el("li", "none", "Nothing today"));
    now.forEach(function (e) {
      var li = el("li", "ev-row");
      li.appendChild(el("b", "t", e.time));
      li.appendChild(document.createTextNode(e.title));
      todayUl.appendChild(li);
    });
    box.appendChild(todayUl);
    box.appendChild(el("hr"));
    box.appendChild(el("h3", "", "Coming up"));
    var upUl = el("ul", "tv-up-list");
    var next = comingUp(ctx.events, ctx.today);
    if (!next.length) upUl.appendChild(el("li", "none", "Nothing coming up"));
    next.forEach(function (u) {
      var li = el("li", "ev-row", u.title);
      li.appendChild(el("small", "", u.when));
      upUl.appendChild(li);
    });
    box.appendChild(upUl);
    card.appendChild(box);
    fitDay(box);
  }

  var WIDGETS = {
    clock: {
      mount: function (card, widget, ctx) {
        var face = el("div", "tv-face");
        face.appendChild(el("div", "time"));
        face.appendChild(el("div", "day"));
        face.appendChild(el("div", "date"));
        card.appendChild(face);
        WIDGETS.clock.update(card, widget, ctx);
      },
      update: function (card, widget, ctx) {
        card.querySelector(".tv-face .time").textContent = formatClock(ctx.now);
        card.querySelector(".tv-face .day").textContent = C.WEEKDAYS[ctx.now.getDay()];
        card.querySelector(".tv-face .date").textContent = fullDate(ctx.now);
      },
      title: function () { return "Clock"; },
    },
    calendar: {
      mount: function (card, widget, ctx) {
        WIDGETS.calendar.update(card, widget, ctx);
      },
      update: function (card, widget, ctx) {
        var old = card.querySelector(".tv-cal, .tv-cal-day");
        if (old) card.removeChild(old);
        if (isFull(widget)) renderMonth(card, ctx);
        else renderDay(card, ctx);
      },
      title: function (widget, ctx) {
        if (!isFull(widget)) return "Today";
        return C.MONTHS[Number(ctx.today.slice(5, 7)) - 1] + " " + ctx.today.slice(0, 4);
      },
    },
    lists: placeholder("Lists"),
    weather: placeholder("Weather"),
  };

  var TvScreen = {
    WIDGETS: WIDGETS,
    NEEDS: NEEDS,
    EMPTY_TEXT: EMPTY_TEXT,
    placement: placement,
    currentScreen: currentScreen,
    needs: needs,
    isFull: isFull,
    formatClock: formatClock,
    formatDate: formatDate,
    fullDate: fullDate,
    whenLabel: whenLabel,
    upcoming: upcoming,
    todayEvents: todayEvents,
    comingUp: comingUp,
    eventRange: eventRange,
    msToMidnight: msToMidnight,
    screenLabel: screenLabel,
  };

  root.TvScreen = TvScreen;
  if (typeof module !== "undefined" && module.exports) module.exports = TvScreen;
  if (typeof document === "undefined") return;

  var screenEl = document.getElementById("screen");
  var titleEl = document.getElementById("title");
  var clockEl = document.getElementById("clock");
  var countEl = document.getElementById("screen-count");
  var todayEl = document.getElementById("today");
  var state = { screens: [], settings: null, data: {}, failed: {} };
  var builtKey = null;
  var loadSeq = 0;
  var backoff = 1;

  function context() {
    var now = new Date();
    return {
      settings: state.settings,
      lists: state.data.lists,
      events: state.data.events,
      weather: state.data.weather,
      now: now,
      today: C.localToday(now),
    };
  }

  function failedFor(widget) {
    return (NEEDS[widget.kind] || []).some(function (key) { return state.failed[key]; });
  }

  function layoutKey(screen) {
    if (!screen) return "none";
    return JSON.stringify([screen.id, screen.widgets.map(function (w) {
      return [w.id, w.kind, w.w, w.h, w.col, w.row, w.list_id, failedFor(w)];
    })]);
  }

  function buildCard(widget, ctx) {
    var card = el("section", "tv-card w-" + widget.kind);
    var place = placement(widget);
    card.style.gridColumn = place.gridColumn;
    card.style.gridRow = place.gridRow;
    card.setAttribute("data-kind", widget.kind);
    card.setAttribute("data-size", widget.w + "x" + widget.h);
    var def = WIDGETS[widget.kind];
    card.appendChild(el("h2", "", def ? def.title(widget, ctx) : "Widget"));
    screenEl.appendChild(card);
    if (!def) message(card, "This widget is not available.");
    else if (failedFor(widget)) message(card, "Could not load. Trying again soon.");
    else def.mount(card, widget, ctx);
    return card;
  }

  function render() {
    if (!state.settings) return;
    var screen = currentScreen(state.screens, state.settings ? state.settings.screen_id : null);
    var ctx = context();
    var key = layoutKey(screen);
    titleEl.textContent = screen ? screen.name : "Home Board";
    countEl.textContent = screenLabel(state.screens, screen);
    var widgets = screen ? screen.widgets : [];
    if (key !== builtKey) {
      builtKey = key;
      screenEl.textContent = "";
      if (!widgets.length) screenEl.appendChild(el("section", "tv-card empty", EMPTY_TEXT));
      widgets.forEach(function (w) { buildCard(w, ctx); });
      return;
    }
    var cards = screenEl.querySelectorAll(".tv-card[data-kind]");
    widgets.forEach(function (w, i) {
      var def = WIDGETS[w.kind];
      var card = cards[i];
      if (!def || !card || failedFor(w)) return;
      card.querySelector("h2").textContent = def.title(w, ctx);
      def.update(card, w, ctx);
    });
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }

  function dataUrl(key) {
    if (key === "events") {
      var range = eventRange(C.localToday(new Date()));
      return "/api/events?start=" + range.start + "&end=" + range.end;
    }
    return "/api/" + key;
  }

  function load() {
    var seq = ++loadSeq;
    return Promise.all([getJSON("/api/settings"), getJSON("/api/screens")]).then(function (res) {
      var screen = currentScreen(res[1], res[0].screen_id);
      var keys = needs(screen);
      return Promise.all(keys.map(function (key) {
        return getJSON(dataUrl(key)).then(function (data) {
          return { key: key, data: data };
        }, function () {
          return { key: key, failed: true };
        });
      })).then(function (parts) {
        if (seq !== loadSeq) return;
        state.settings = res[0];
        state.screens = res[1];
        state.failed = {};
        parts.forEach(function (p) {
          if (p.failed) state.failed[p.key] = true;
          else state.data[p.key] = p.data;
        });
        render();
      });
    }).catch(function () {});
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
    ["hello", "settings_changed", "lists_changed", "events_changed", "weather_changed"].forEach(function (name) {
      es.addEventListener(name, load);
    });
    es.onerror = function () {
      es.close();
      setTimeout(connect, backoff * 1000);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_S);
    };
  }

  function updateClock() {
    var now = new Date();
    clockEl.textContent = formatClock(now);
    todayEl.textContent = formatDate(now);
    render();
    setTimeout(updateClock, 60000 - now.getSeconds() * 1000 - now.getMilliseconds() + 20);
  }

  function atMidnight() {
    load();
    setTimeout(atMidnight, msToMidnight(new Date()));
  }

  window.addEventListener("resize", render);
  updateClock();
  setTimeout(atMidnight, msToMidnight(new Date()));
  load().then(connect);
})(typeof window !== "undefined" ? window : globalThis);
