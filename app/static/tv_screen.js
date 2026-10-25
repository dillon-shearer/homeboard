(function (root) {
  "use strict";

  var C = root.Calendar || require("./calendar_util.js");
  var MAX_BACKOFF_S = 30;
  var UPCOMING_DAYS = 7;
  var FORECAST_DAYS = 7;
  var MAX_UPCOMING = 10;
  var SHORT_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var NEEDS = { calendar: ["events"], lists: ["lists"], weather: ["weather"] };
  var EMPTY_TEXT = "Nothing on this screen yet. Add widgets from your phone.";
  var SPOT_TEXT = "Empty spot. Add a widget on your phone: TV, Edit, then the screen name.";
  var GRID_COLS = 2;
  var GRID_ROWS = 2;
  var NO_OPEN_TEXT = "No lists open. Tick Open on the TV in a list's options on your phone.";
  var NO_LISTS_TEXT = "No lists yet. Add one from your phone.";
  var TAP_TEXT = "Tap a list above to open it.";
  var PICK_TEXT = "Pick a list on your phone: TV, Edit, then this screen.";
  var NO_WEATHER_TEXT = "No forecast yet";
  var NO_WEATHER_HINT = "Check the weather location on your phone: TV page, Weather.";
  var TAP_SLOP_PX = 10;
  var SWIPE_CLICK_MS = 600;
  var SLIDE_MS = 250;
  var SPOTIFY_POLL_MS = 3000;
  var SPOTIFY_TICK_MS = 1000;
  var SPOTIFY_AFTER_TAP_MS = 1000;
  var SPOTIFY_EXTRA_EVERY = 10;
  var SPOTIFY_NOTE_MS = 3000;
  var VOLUME_STEP = 5;
  var VOLUME_CHAIN_MS = 1000;
  var SPOTIFY_LAYOUTS = { "2x2": "full", "2x1": "wide", "1x2": "tall", "1x1": "tile" };
  var NOT_LINKED_TEXT = "Spotify not linked";
  var IDLE_TEXT = "Nothing playing";
  var NO_PLAYLISTS_TEXT = "Pick playlists on your phone: Sound.";
  var SPOTIFY_DOWN_TEXT = "Could not reach Spotify";
  var SPEAKER_DOWN_TEXT = "Could not reach the speaker";

  function placement(widget) {
    return {
      gridColumn: (widget.col + 1) + " / span " + widget.w,
      gridRow: (widget.row + 1) + " / span " + widget.h,
    };
  }

  function freeCells(widgets) {
    var taken = {};
    (widgets || []).forEach(function (w) {
      for (var c = w.col; c < w.col + w.w; c++) {
        for (var r = w.row; r < w.row + w.h; r++) taken[c + "," + r] = true;
      }
    });
    var out = [];
    for (var r = 0; r < GRID_ROWS; r++) {
      for (var c = 0; c < GRID_COLS; c++) {
        if (!taken[c + "," + r]) out.push({ col: c, row: r, w: 1, h: 1 });
      }
    }
    return out;
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
    (Array.isArray(screen) ? screen : [screen]).forEach(function (one) {
      ((one && one.widgets) || []).forEach(function (w) {
        (NEEDS[w.kind] || []).forEach(function (key) {
          if (out.indexOf(key) === -1) out.push(key);
        });
      });
    });
    return out;
  }

  function neighbourSide(dx) {
    if (dx < 0) return "right";
    return dx > 0 ? "left" : null;
  }

  function slideX(px) {
    return "translateX(" + px + "px)";
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

  function longDate(iso) {
    return C.dayTitle(iso) + ", " + iso.slice(0, 4);
  }

  function comingUp(occurrences, today) {
    return between(occurrences, C.addDays(today, 1), C.addDays(today, UPCOMING_DAYS)).map(function (o) {
      return { title: o.title, when: whenLabel(o, today) };
    });
  }

  function dayLabel(iso) {
    var d = C.parseIso(iso);
    return SHORT_DAYS[d.getUTCDay()] + ", " + SHORT_MONTHS[d.getUTCMonth()] + " " + d.getUTCDate();
  }

  function nextEvent(occurrences, today) {
    return comingUp(occurrences, today)[0] || null;
  }

  function weekDays(occurrences, today) {
    var out = [];
    for (var i = 0; i < UPCOMING_DAYS; i++) {
      var date = C.addDays(today, i);
      var d = C.parseIso(date);
      out.push({
        date: date,
        name: SHORT_DAYS[d.getUTCDay()],
        day: d.getUTCDate(),
        today: i === 0,
        chips: between(occurrences, date, date).map(chipText),
      });
    }
    return out;
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

  function sizeOf(widget) {
    return widget.w + "x" + widget.h;
  }

  function rank(item) {
    if (item.done) return 2;
    return item.pinned ? 0 : 1;
  }

  function sortItems(items) {
    return (items || []).map(function (item, i) {
      return { item: item, i: i };
    }).sort(function (a, b) {
      return rank(a.item) - rank(b.item) || (a.item.sort_order || 0) - (b.item.sort_order || 0) || a.i - b.i;
    }).map(function (x) {
      return x.item;
    });
  }

  function shownLists(lists, widget) {
    if (sizeOf(widget) === "1x1") {
      return (lists || []).filter(function (l) { return l.id === widget.list_id; }).slice(0, 1);
    }
    return (lists || []).filter(function (l) { return l.tv_open; });
  }

  function tabLists(lists, widget) {
    if (!isFull(widget)) return [];
    return (lists || []).filter(function (l) { return !l.tv_open; });
  }

  function openCount(list) {
    return (list.items || []).filter(function (i) { return !i.done; }).length;
  }

  function tabLabel(list) {
    return list.name + " " + openCount(list);
  }

  function splitTracks(n) {
    return "repeat(" + Math.max(1, n) + ", minmax(0, 1fr))";
  }

  function moreLabel(hidden, done) {
    return "+" + hidden + " more" + (done ? ", " + done + " done" : "");
  }

  function num(v) {
    return v === null || v === undefined ? "--" : String(v);
  }

  function weatherToday(weather, today) {
    var days = (weather && weather.days) || [];
    for (var i = 0; i < days.length; i++) if (days[i].date === today) return i;
    return days.length ? 0 : -1;
  }

  function hiLo(day) {
    return "H " + num(day.high) + " L " + num(day.low);
  }

  function rainText(day) {
    return "Rain " + num(day.rain) + "%";
  }

  function nextDays(weather, today) {
    var i = weatherToday(weather, today);
    if (i < 0) return [];
    return weather.days.slice(i, i + FORECAST_DAYS).map(function (d, n) {
      return {
        name: SHORT_DAYS[C.parseIso(d.date).getUTCDay()],
        icon: d.icon,
        hiLo: hiLo(d),
        rain: num(d.rain) + "%",
        today: n === 0,
      };
    });
  }

  function clockAt(iso) {
    return iso && iso.length >= 16 ? C.formatTime(iso.slice(11, 16)) : "--";
  }

  function asOf(fetchedAt) {
    var d = new Date(fetchedAt);
    return isNaN(d.getTime()) ? "" : "as of " + formatClock(d);
  }

  function formatMs(ms) {
    var s = Math.max(0, Math.floor((ms || 0) / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function spotifyLayout(widget) {
    return SPOTIFY_LAYOUTS[sizeOf(widget)] || "tile";
  }

  function spotifyState(now) {
    if (now && now.linked === false) return "unlinked";
    if (!now || !now.track) return "idle";
    return now.playing ? "playing" : "paused";
  }

  function coverUrl(now) {
    var kind = spotifyState(now);
    if (kind !== "playing" && kind !== "paused") return null;
    return typeof now.cover === "string" && now.cover ? now.cover : null;
  }

  function progressAt(now, sinceMs) {
    if (!now || typeof now.progress_ms !== "number") return 0;
    var ms = now.progress_ms + (now.playing ? Math.max(0, sinceMs || 0) : 0);
    return now.duration_ms ? Math.min(ms, now.duration_ms) : ms;
  }

  function volumeStep(volume, delta) {
    if (typeof volume !== "number") return null;
    return Math.max(0, Math.min(100, volume + delta));
  }

  function speakerOn(sound) {
    return !!(sound && sound.speaker && sound.speaker.connected);
  }

  var CLOUD = '<path d="M18 52h30a10 10 0 0 0 0-20a13 13 0 0 0-25-4a10 10 0 0 0-5 24z" ' +
    'fill="#ffffff" stroke="#000000" stroke-width="2"/>';
  var CLOUD_UP = '<g transform="translate(0,-10)">' + CLOUD + "</g>";
  var SUN = '<path d="M48 32h8M8 32h8M32 8v8M32 48v8M43 43l6 6M15 15l6 6M43 21l6-6M15 49l6-6" ' +
    'stroke="#000000" stroke-width="3"/><circle cx="32" cy="32" r="12" fill="#ffff00" stroke="#000000" stroke-width="2"/>';
  var MOON = '<path d="M40 10a22 22 0 1 0 14 34a18 18 0 1 1-14-34z" fill="#ffff00" stroke="#000000" stroke-width="2"/>';
  var RAIN = '<path d="M26 46l-4 10M36 46l-4 10M46 46l-4 10" stroke="#0000ff" stroke-width="3"/>';
  var DRIZZLE = '<path d="M26 48l-2 4M36 48l-2 4M46 48l-2 4M30 56l-2 4M40 56l-2 4" stroke="#0000ff" stroke-width="3"/>';
  var SNOW = '<path d="M24 46v10M19 51h10M40 46v10M35 51h10M32 54v8M28 58h8" stroke="#000080" stroke-width="2"/>';
  var ICONS = {
    sun: SUN,
    moon: MOON,
    cloud: CLOUD,
    "cloud-sun": '<g transform="translate(-4,-6) scale(0.75)">' + SUN + "</g>" + CLOUD,
    "cloud-moon": '<g transform="translate(-6,-6) scale(0.7)">' + MOON + "</g>" + CLOUD,
    fog: '<path d="M10 22h44M6 32h52M10 42h44M14 52h36" stroke="#808080" stroke-width="5"/>',
    drizzle: CLOUD_UP + DRIZZLE,
    rain: CLOUD_UP + RAIN,
    sleet: CLOUD_UP + '<path d="M26 46l-4 10M46 46l-4 10" stroke="#0000ff" stroke-width="3"/>' +
      '<path d="M35 48v8M31 52h8" stroke="#000080" stroke-width="2"/>',
    snow: CLOUD_UP + SNOW,
    thunder: CLOUD_UP + '<path d="M36 40l-10 13h7l-4 11 13-15h-7l5-9z" fill="#ffff00" stroke="#000000" stroke-width="1.5"/>',
  };

  var SP_ICONS = {
    play: '<path d="M8 4v16l12-8z"/>',
    pause: '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>',
    previous: '<path d="M5 4h3v16H5zM9 12l11-8v16z"/>',
    next: '<path d="M16 4h3v16h-3zM4 4l11 8-11 8z"/>',
    speaker: '<path d="M3 9h4l6-5v16l-6-5H3z"/>',
    note: '<path d="M9 18V6l11-3v12" fill="none" stroke="#000000" stroke-width="2"/>' +
      '<circle cx="6" cy="18" r="3"/><circle cx="17" cy="15" r="3"/>',
  };

  function spotSvg(name) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + SP_ICONS[name] + "</svg>";
  }

  function iconSvg(name) {
    return '<svg viewBox="0 0 64 64" aria-hidden="true">' + (ICONS[name] || CLOUD) + "</svg>";
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
    fitList(up);
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

  function renderOne(card, ctx) {
    var box = el("div", "tv-cal-day one");
    var todayUl = el("ul", "today-list");
    var now = todayEvents(ctx.events, ctx.today);
    now.forEach(function (e) {
      var li = el("li", "ev-row");
      li.appendChild(el("b", "t", e.time));
      li.appendChild(document.createTextNode(e.title));
      todayUl.appendChild(li);
    });
    box.appendChild(todayUl);
    if (!now.length) {
      todayUl.appendChild(el("li", "none", "Nothing today"));
      var upUl = el("ul", "tv-up-list");
      var next = nextEvent(ctx.events, ctx.today);
      if (next) {
        var li = el("li", "ev-row", next.title);
        li.appendChild(el("small", "", next.when));
        upUl.appendChild(li);
      } else {
        upUl.appendChild(el("li", "none", "Nothing coming up"));
      }
      box.appendChild(upUl);
    }
    card.appendChild(box);
    fitDay(box);
  }

  function renderWeek(card, ctx) {
    var grid = el("div", "tv-cal-grid tv-week");
    weekDays(ctx.events, ctx.today).forEach(function (w) {
      var cell = el("div", "d" + (w.today ? " today" : ""));
      cell.setAttribute("data-date", w.date);
      var head = el("div", "dh");
      head.appendChild(el("span", "wd", w.name));
      head.appendChild(el("b", "", String(w.day)));
      cell.appendChild(head);
      var list = el("div", "chips");
      w.chips.forEach(function (text) {
        list.appendChild(el("i", "ev", text));
      });
      cell.appendChild(list);
      grid.appendChild(cell);
    });
    card.appendChild(grid);
    fitChips(grid);
  }

  function itemList(list) {
    var notes = list.kind === "notes";
    var ul = el("ul");
    var items = sortItems(list.items);
    if (!items.length) ul.appendChild(el("li", "none", "Nothing here"));
    items.forEach(function (item) {
      var li = el("li", item.done ? "done" : item.pinned ? "pin" : "");
      if (!notes) li.setAttribute("data-item", item.id);
      if (!notes && (item.done || !item.pinned)) li.appendChild(el("span", item.done ? "box tick" : "box"));
      li.appendChild(document.createTextNode(item.text));
      ul.appendChild(li);
    });
    return ul;
  }

  function fitList(box) {
    var ul = box.querySelector("ul");
    if (!ul) return;
    var rows = Array.prototype.slice.call(ul.children);
    var more = null;
    var hidden = 0;
    var done = 0;
    for (var i = rows.length - 1; i >= 0 && ul.scrollHeight > ul.clientHeight; i--) {
      if (rows[i].classList.contains("pin")) continue;
      rows[i].hidden = true;
      hidden++;
      if (rows[i].classList.contains("done")) done++;
      if (!more) more = box.appendChild(el("div", "more"));
      more.textContent = moreLabel(hidden, done);
    }
  }

  function listCard(list, closable) {
    var card = el("section", "tv-card tv-list" + (list.kind === "notes" ? " notes" : ""));
    var h2 = el("h2", "", list.name);
    if (closable) h2.setAttribute("data-close", list.id);
    card.appendChild(h2);
    card.appendChild(itemList(list));
    return card;
  }

  function renderLists(card, widget, ctx) {
    var old = card.querySelector(".tv-lists-body");
    var sig = JSON.stringify([sizeOf(widget), widget.list_id, ctx.lists || [], innerWidth, innerHeight]);
    if (old && old.getAttribute("data-sig") === sig) return;
    if (old) card.removeChild(old);
    var body = el("div", "tv-lists-body");
    body.setAttribute("data-sig", sig);
    card.appendChild(body);
    var lists = ctx.lists || [];
    var full = isFull(widget);
    if (sizeOf(widget) === "1x1") {
      var one = shownLists(lists, widget)[0];
      card.classList.toggle("notes", !!one && one.kind === "notes");
      if (!one) {
        body.appendChild(el("p", "tv-msg", PICK_TEXT));
        return;
      }
      body.appendChild(itemList(one));
      fitList(body);
      return;
    }
    var tabs = tabLists(lists, widget);
    if (tabs.length) {
      var row = el("div", "tv-tabs");
      tabs.forEach(function (l) {
        var tab = el("button", "tv-tab", tabLabel(l));
        tab.type = "button";
        tab.setAttribute("data-open", l.id);
        row.appendChild(tab);
      });
      body.appendChild(row);
    }
    var open = shownLists(lists, widget);
    if (!open.length) {
      var text = NO_OPEN_TEXT;
      if (full) text = lists.length ? TAP_TEXT : NO_LISTS_TEXT;
      body.appendChild(el("p", "tv-msg", text));
      return;
    }
    var grid = el("div", "tv-lists" + (full ? "" : " condensed"));
    if (sizeOf(widget) === "1x2") grid.style.gridTemplateRows = splitTracks(open.length);
    else grid.style.gridTemplateColumns = splitTracks(open.length);
    open.forEach(function (l) { grid.appendChild(listCard(l, full)); });
    body.appendChild(grid);
    Array.prototype.forEach.call(grid.children, fitList);
  }

  function weatherIcon(name) {
    var span = el("span", "wx-icon");
    span.innerHTML = iconSvg(name);
    return span;
  }

  function renderWeather(card, widget, ctx) {
    var old = card.querySelector(".tv-wx");
    if (old) card.removeChild(old);
    var w = ctx.weather;
    var wx = el("div", "tv-wx");
    card.appendChild(wx);
    if (!w || !w.ok) {
      wx.classList.add("none");
      wx.appendChild(el("p", "wx-label", NO_WEATHER_TEXT));
      if (w && w.location) wx.appendChild(el("p", "wx-where", w.location));
      wx.appendChild(el("p", "wx-hint", NO_WEATHER_HINT));
      return;
    }
    var cur = w.current || {};
    var main = el("div", "wx-main");
    var now = el("div", "wx-now");
    now.appendChild(weatherIcon(cur.icon));
    now.appendChild(el("span", "wx-temp", num(cur.temp) + "°"));
    main.appendChild(now);
    main.appendChild(el("div", "wx-label", cur.label || ""));
    var i = weatherToday(w, ctx.today);
    var today = i < 0 ? null : w.days[i];
    if (today) {
      main.appendChild(el("div", "wx-hilo", hiLo(today)));
      main.appendChild(el("div", "wx-rain", rainText(today)));
    }
    if (w.stale) main.appendChild(el("div", "wx-asof", asOf(w.fetched_at)));
    wx.appendChild(main);
    var size = sizeOf(widget);
    if (size === "1x1") return;
    var next = nextDays(w, ctx.today);
    if (next.length) wx.appendChild(forecastDays(next, size === "1x2" ? "wx-rows" : "wx-strip"));
    if (size === "2x2" && today) {
      var sun = el("div", "wx-sun");
      sun.appendChild(el("span", "", "Sunrise " + clockAt(today.sunrise)));
      sun.appendChild(el("span", "", "Sunset " + clockAt(today.sunset)));
      wx.appendChild(sun);
    }
  }

  function forecastDays(next, cls) {
    var days = el("div", "wx-days " + cls);
    next.forEach(function (d) {
      var box = el("div", d.today ? "wx-day today" : "wx-day");
      box.appendChild(el("span", "wx-dname", d.name));
      box.appendChild(weatherIcon(d.icon));
      box.appendChild(el("span", "wx-dhilo", d.hiLo));
      box.appendChild(el("span", "wx-drain", d.rain));
      days.appendChild(box);
    });
    return days;
  }

  function spotButton(action, label, icon, text) {
    var b = el("button", "sp-btn sp-" + action);
    b.type = "button";
    b.setAttribute("data-sp", action);
    b.setAttribute("aria-label", label);
    if (icon) b.innerHTML = spotSvg(icon);
    if (text) b.appendChild(el("span", "", text));
    return b;
  }

  function spotImage(cls, url) {
    var box = el(cls === "sp-cover" ? "div" : "span", cls + (url ? "" : " none"));
    if (url) {
      var img = el("img");
      img.alt = "";
      img.src = url;
      box.appendChild(img);
    } else {
      box.innerHTML = spotSvg("note");
    }
    return box;
  }

  function toggleButton(kind) {
    return kind === "playing" ? spotButton("toggle", "Pause", "pause") : spotButton("toggle", "Play", "play");
  }

  function playHere() {
    return spotButton("here", "Play here", "play", "Play here");
  }

  function transport(kind) {
    var row = el("div", "sp-transport");
    row.appendChild(spotButton("previous", "Previous", "previous"));
    row.appendChild(toggleButton(kind));
    row.appendChild(spotButton("next", "Next", "next"));
    return row;
  }

  function speakerButton(sp) {
    var on = speakerOn(sp.sound);
    var text = on ? "Speaker connected" : "Speaker not connected";
    if (sp.connecting) text = on ? "Disconnecting..." : "Connecting...";
    var b = spotButton("speaker", text, "speaker", text);
    if (on) b.classList.add("on");
    return b;
  }

  function playlistRows(shown) {
    var ul = el("ul", "sp-pls");
    if (!shown || !shown.length) ul.appendChild(el("li", "none", NO_PLAYLISTS_TEXT));
    (shown || []).forEach(function (p) {
      var li = el("li");
      var b = el("button", "sp-pl");
      b.type = "button";
      b.setAttribute("data-playlist", p.id);
      b.appendChild(spotImage("sp-thumb", p.cover));
      b.appendChild(el("span", "sp-name", p.name || "Playlist"));
      li.appendChild(b);
      ul.appendChild(li);
    });
    return ul;
  }

  function spotBody(layout, kind, now, sp) {
    var box = el("div", "sp sp-" + layout + " " + kind);
    if (kind === "unlinked") {
      box.appendChild(el("p", "tv-msg", NOT_LINKED_TEXT));
      return box;
    }
    var live = kind !== "idle";
    var here = !live && !!now && !now.device;
    var cover = spotImage("sp-cover", coverUrl(now));
    box.appendChild(cover);
    if (layout === "tile") {
      if (live) cover.appendChild(toggleButton(kind));
      if (here) cover.appendChild(playHere());
      box.appendChild(el("div", "sp-track", live ? now.track : IDLE_TEXT));
    } else {
      var info = el("div", "sp-info");
      var head = info.appendChild(el("div", "sp-head"));
      var controls = info.appendChild(el("div", "sp-controls"));
      head.appendChild(el("div", "sp-track", live ? now.track : IDLE_TEXT));
      if (live && now.artist) head.appendChild(el("div", "sp-artist", now.artist));
      if (live && layout === "full") {
        var prog = el("div", "sp-progress");
        prog.appendChild(el("span", "sp-elapsed", "0:00"));
        prog.appendChild(el("span", "sp-bar")).appendChild(el("i"));
        prog.appendChild(el("span", "sp-total", formatMs(now.duration_ms)));
        controls.appendChild(prog);
      }
      if (live) controls.appendChild(transport(kind));
      if (here) controls.appendChild(el("div", "sp-transport")).appendChild(playHere());
      if (layout === "full") {
        if (live) {
          var known = typeof now.volume === "number";
          var vol = el("div", "sp-volume");
          vol.appendChild(spotButton("vol-down", "Volume down", null, "-")).disabled = !known;
          vol.appendChild(el("span", "sp-volbar")).appendChild(el("i")).style.width = (known ? now.volume : 0) + "%";
          vol.appendChild(spotButton("vol-up", "Volume up", null, "+")).disabled = !known;
          vol.appendChild(el("span", "sp-vol", known ? now.volume + "%" : "--%"));
          controls.appendChild(vol);
        }
        controls.appendChild(speakerButton(sp));
      }
      box.appendChild(info);
      if (layout === "full") {
        var side = el("div", "sp-side");
        side.appendChild(el("div", "sp-label", "Playlists"));
        side.appendChild(playlistRows(sp.shown));
        box.appendChild(side);
      }
    }
    if (sp.note) box.appendChild(el("p", "sp-msg", sp.note));
    return box;
  }

  function renderSpotify(card, widget, ctx) {
    var sp = ctx.spotify || {};
    var now = sp.now;
    var kind = spotifyState(now);
    var layout = spotifyLayout(widget);
    var full = layout === "full";
    var sig = JSON.stringify([layout, kind, kind === "unlinked" || !now ? null : [now.track, now.artist, now.cover,
      now.duration_ms, now.volume, now.device], full ? [sp.shown || [], speakerOn(sp.sound), !!sp.connecting] : null, sp.note || null]);
    var body = card.querySelector(".sp");
    if (!body || body.getAttribute("data-sig") !== sig) {
      var oldList = body && body.querySelector(".sp-pls");
      var scroll = oldList ? oldList.scrollTop : 0;
      if (body) card.removeChild(body);
      body = spotBody(layout, kind, now, sp);
      body.setAttribute("data-sig", sig);
      card.appendChild(body);
      var list = body.querySelector(".sp-pls");
      if (list) list.scrollTop = scroll;
    }
    var bar = body.querySelector(".sp-bar i");
    if (!bar) return;
    var ms = progressAt(now, sp.since);
    bar.style.width = (now.duration_ms ? Math.min(100, ms / now.duration_ms * 100) : 0) + "%";
    body.querySelector(".sp-elapsed").textContent = formatMs(ms);
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
        var old = card.querySelector(".tv-cal, .tv-cal-day, .tv-week");
        if (old) card.removeChild(old);
        var size = sizeOf(widget);
        if (size === "2x2") renderMonth(card, ctx);
        else if (size === "2x1") renderWeek(card, ctx);
        else if (size === "1x1") renderOne(card, ctx);
        else renderDay(card, ctx);
      },
      title: function (widget, ctx) {
        var size = sizeOf(widget);
        if (size === "1x1") return dayLabel(ctx.today);
        if (size === "2x1") return "This week";
        if (size !== "2x2") return "Today";
        return C.MONTHS[Number(ctx.today.slice(5, 7)) - 1] + " " + ctx.today.slice(0, 4);
      },
    },
    lists: {
      mount: function (card, widget, ctx) {
        WIDGETS.lists.update(card, widget, ctx);
      },
      update: renderLists,
      title: function (widget, ctx) {
        if (sizeOf(widget) !== "1x1") return "Lists";
        var one = shownLists(ctx.lists, widget)[0];
        return one ? one.name : "Lists";
      },
    },
    weather: {
      mount: function (card, widget, ctx) {
        WIDGETS.weather.update(card, widget, ctx);
      },
      update: renderWeather,
      title: function (widget, ctx) {
        var w = ctx.weather;
        return w && w.ok && w.location ? w.location : "Weather";
      },
    },
    spotify: {
      mount: function (card, widget, ctx) {
        WIDGETS.spotify.update(card, widget, ctx);
      },
      update: renderSpotify,
      title: function () { return "Spotify"; },
    },
  };

  var TvScreen = {
    WIDGETS: WIDGETS,
    NEEDS: NEEDS,
    EMPTY_TEXT: EMPTY_TEXT,
    SPOT_TEXT: SPOT_TEXT,
    placement: placement,
    freeCells: freeCells,
    currentScreen: currentScreen,
    needs: needs,
    neighbourSide: neighbourSide,
    slideX: slideX,
    isFull: isFull,
    formatClock: formatClock,
    formatDate: formatDate,
    fullDate: fullDate,
    whenLabel: whenLabel,
    upcoming: upcoming,
    chipText: chipText,
    todayEvents: todayEvents,
    dayEvents: todayEvents,
    longDate: longDate,
    comingUp: comingUp,
    dayLabel: dayLabel,
    nextEvent: nextEvent,
    weekDays: weekDays,
    eventRange: eventRange,
    msToMidnight: msToMidnight,
    screenLabel: screenLabel,
    sortItems: sortItems,
    shownLists: shownLists,
    tabLists: tabLists,
    tabLabel: tabLabel,
    splitTracks: splitTracks,
    moreLabel: moreLabel,
    itemList: itemList,
    weatherToday: weatherToday,
    hiLo: hiLo,
    rainText: rainText,
    nextDays: nextDays,
    clockAt: clockAt,
    asOf: asOf,
    iconSvg: iconSvg,
    formatMs: formatMs,
    spotifyLayout: spotifyLayout,
    spotifyState: spotifyState,
    coverUrl: coverUrl,
    progressAt: progressAt,
    volumeStep: volumeStep,
    speakerOn: speakerOn,
  };

  root.TvScreen = TvScreen;
  if (typeof module !== "undefined" && module.exports) module.exports = TvScreen;
  if (typeof document === "undefined") return;

  var screenEl = document.getElementById("screen");
  var titleEl = document.getElementById("title");
  var clockEl = document.getElementById("clock");
  var countEl = document.getElementById("screen-count");
  var todayEl = document.getElementById("today");
  var dayDialog = document.getElementById("day-dialog");
  var dayTitle = document.getElementById("day-title");
  var dayList = document.getElementById("day-list");
  var dayChannel = null;
  var state = { screens: [], settings: null, data: {}, failed: {} };
  var builtKey = null;
  var loadSeq = 0;
  var backoff = 1;
  var touchFrom = null;
  var lastSwipe = 0;
  var drag = null;
  var sliding = false;
  var spot = { on: false, seq: 0, polls: 0, timer: null, tick: null, now: null, at: 0, shown: [], sound: null,
    note: null, noteTimer: null, connecting: false, vol: null };

  function context() {
    var now = new Date();
    return {
      settings: state.settings,
      lists: state.data.lists,
      events: state.data.events,
      weather: state.data.weather,
      spotify: {
        now: spot.now,
        since: now.getTime() - spot.at,
        shown: spot.shown,
        sound: spot.sound,
        note: spot.note,
        connecting: spot.connecting,
      },
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

  function buildCard(widget, ctx, grid) {
    var card = el("section", "tv-card w-" + widget.kind);
    var place = placement(widget);
    card.style.gridColumn = place.gridColumn;
    card.style.gridRow = place.gridRow;
    card.setAttribute("data-kind", widget.kind);
    card.setAttribute("data-size", widget.w + "x" + widget.h);
    var def = WIDGETS[widget.kind];
    card.appendChild(el("h2", "", def ? def.title(widget, ctx) : "Widget"));
    grid.appendChild(card);
    if (!def) message(card, "This widget is not available.");
    else if (failedFor(widget)) message(card, "Could not load. Trying again soon.");
    else def.mount(card, widget, ctx);
    return card;
  }

  function buildSpot(cell, grid) {
    var card = el("section", "tv-card spot");
    var place = placement(cell);
    card.style.gridColumn = place.gridColumn;
    card.style.gridRow = place.gridRow;
    message(card, SPOT_TEXT);
    grid.appendChild(card);
  }

  function fill(grid, widgets, ctx) {
    grid.textContent = "";
    if (!widgets.length) grid.appendChild(el("section", "tv-card empty", EMPTY_TEXT));
    widgets.forEach(function (w) { buildCard(w, ctx, grid); });
    if (widgets.length) freeCells(widgets).forEach(function (cell) { buildSpot(cell, grid); });
  }

  function render() {
    if (!state.settings || drag) return;
    var screen = currentScreen(state.screens, state.settings ? state.settings.screen_id : null);
    var ctx = context();
    var key = layoutKey(screen);
    titleEl.textContent = screen ? screen.name : "Home Board";
    countEl.textContent = screenLabel(state.screens, screen);
    var widgets = screen ? screen.widgets : [];
    if (state.settings.channel !== dayChannel) closeDay();
    syncSpotify();
    if (key !== builtKey) {
      builtKey = key;
      closeDay();
      fill(screenEl, widgets, ctx);
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

  function spotifyWidgets() {
    if (!state.settings) return [];
    var screen = currentScreen(state.screens, state.settings.screen_id);
    return screen ? screen.widgets.filter(function (w) { return w.kind === "spotify"; }) : [];
  }

  function spotifyWanted() {
    return document.visibilityState !== "hidden" && !!state.settings && state.settings.channel === "screen" &&
      spotifyWidgets().length > 0;
  }

  function syncSpotify() {
    var want = spotifyWanted();
    if (want === spot.on) return;
    spot.on = want;
    spot.seq++;
    clearTimeout(spot.timer);
    clearInterval(spot.tick);
    spot.timer = spot.tick = null;
    if (!want) return;
    spot.polls = 0;
    spot.tick = setInterval(renderSpotifyCards, SPOTIFY_TICK_MS);
    pollSpotify();
  }

  function renderSpotifyCards() {
    if (!state.settings || drag) return;
    var screen = currentScreen(state.screens, state.settings.screen_id);
    if (!screen || layoutKey(screen) !== builtKey) return;
    var ctx = context();
    var cards = screenEl.querySelectorAll(".tv-card[data-kind]");
    screen.widgets.forEach(function (w, i) {
      if (w.kind === "spotify" && cards[i]) WIDGETS.spotify.update(cards[i], w, ctx);
    });
  }

  function pollSpotify() {
    var seq = ++spot.seq;
    clearTimeout(spot.timer);
    spot.timer = null;
    if (!spot.on) return;
    var jobs = [getJSON("/api/spotify/now").then(function (now) {
      if (seq !== spot.seq) return;
      spot.now = now;
      spot.at = Date.now();
    })];
    if (spotifyWidgets().some(isFull) && spot.polls % SPOTIFY_EXTRA_EVERY === 0) {
      jobs.push(getJSON("/api/spotify/shown").then(function (shown) {
        if (seq === spot.seq) spot.shown = shown;
      }));
      jobs.push(getJSON("/api/sound/status").then(function (sound) {
        if (seq === spot.seq && !spot.connecting) spot.sound = sound;
      }));
    }
    spot.polls++;
    Promise.all(jobs.map(function (job) { return job.catch(function () {}); })).then(function () {
      if (seq !== spot.seq || !spot.on) return;
      renderSpotifyCards();
      spot.timer = setTimeout(pollSpotify, SPOTIFY_POLL_MS);
    });
  }

  function spotifyNote(text) {
    spot.note = text;
    clearTimeout(spot.noteTimer);
    spot.noteTimer = setTimeout(function () {
      spot.note = null;
      renderSpotifyCards();
    }, SPOTIFY_NOTE_MS);
    renderSpotifyCards();
  }

  function errorText(r, fallback) {
    return r.json().catch(function () { return {}; }).then(function (out) {
      return r.status === 409 && typeof out.detail === "string" ? out.detail : fallback;
    });
  }

  function spotifyCall(method, url, body) {
    spot.seq++;
    clearTimeout(spot.timer);
    renderSpotifyCards();
    var opts = { method: method };
    if (body) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      if (!r.ok) return errorText(r, SPOTIFY_DOWN_TEXT).then(spotifyNote);
    }, function () {
      spotifyNote(SPOTIFY_DOWN_TEXT);
    }).then(function () {
      if (spot.on) spot.timer = setTimeout(pollSpotify, SPOTIFY_AFTER_TAP_MS);
    });
  }

  function toggleSpeaker() {
    if (spot.connecting) return;
    var on = speakerOn(spot.sound);
    spot.connecting = true;
    renderSpotifyCards();
    fetch(on ? "/api/sound/disconnect" : "/api/sound/connect", { method: "POST" }).then(function (r) {
      if (!r.ok) return errorText(r, SPEAKER_DOWN_TEXT).then(spotifyNote);
      return r.json().then(function (out) {
        var speaker = Object.assign({}, spot.sound && spot.sound.speaker, { connected: !!out.connected });
        spot.sound = Object.assign({}, spot.sound, { speaker: speaker });
      });
    }).catch(function () {
      spotifyNote(SPEAKER_DOWN_TEXT);
    }).then(function () {
      spot.connecting = false;
      renderSpotifyCards();
    });
  }

  function freshVolume() {
    spot.seq++;
    clearTimeout(spot.timer);
    return getJSON("/api/spotify/now?fresh=1").then(function (now) {
      return now.volume;
    }, function () {
      spotifyNote(SPOTIFY_DOWN_TEXT);
      return null;
    });
  }

  function volumeTap(delta) {
    var chained = spot.vol && Date.now() - spot.vol.at < VOLUME_CHAIN_MS;
    var sent = (chained ? spot.vol.sent : freshVolume()).then(function (volume) {
      var next = volumeStep(volume, delta);
      if (next === null) {
        clearTimeout(spot.timer);
        if (spot.on) spot.timer = setTimeout(pollSpotify, SPOTIFY_AFTER_TAP_MS);
        return null;
      }
      if (spot.now) spot.now.volume = next;
      spotifyCall("PUT", "/api/spotify/volume", { percent: next });
      return next;
    });
    spot.vol = { sent: sent, at: Date.now() };
    spot.now.volume = volumeStep(spot.now.volume, delta);
    renderSpotifyCards();
  }

  function spotifyTap(btn) {
    var now = spot.now || {};
    if (btn.hasAttribute("data-playlist")) {
      spotifyCall("POST", "/api/spotify/play-playlist", { id: btn.getAttribute("data-playlist") });
      return;
    }
    var action = btn.getAttribute("data-sp");
    if (action === "speaker") {
      toggleSpeaker();
    } else if (action === "toggle") {
      var playing = !!now.playing;
      now.progress_ms = progressAt(now, Date.now() - spot.at);
      now.playing = !playing;
      spot.at = Date.now();
      spotifyCall("POST", playing ? "/api/spotify/pause" : "/api/spotify/play");
    } else if (action === "here") {
      spotifyCall("POST", "/api/spotify/play");
    } else if (action === "previous" || action === "next") {
      spotifyCall("POST", "/api/spotify/" + action);
    } else if (action === "vol-down" || action === "vol-up") {
      if (btn.disabled || typeof now.volume !== "number") return;
      volumeTap(action === "vol-up" ? VOLUME_STEP : -VOLUME_STEP);
    }
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
      var keys = needs(res[1]);
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

  function setOpen(id, open) {
    (state.data.lists || []).forEach(function (l) {
      if (l.id === id) l.tv_open = open;
    });
    render();
    fetch("/api/lists/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tv_open: open }),
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
    }).catch(load);
  }

  function setDone(id, done) {
    (state.data.lists || []).forEach(function (l) {
      (l.items || []).forEach(function (item) {
        if (item.id === id) item.done = done;
      });
    });
    render();
    fetch("/api/items/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ done: done }),
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
    }).catch(load);
  }

  function openDay(date) {
    dayTitle.textContent = longDate(date);
    dayList.textContent = "";
    var events = todayEvents(state.data.events, date);
    if (!events.length) dayList.appendChild(el("li", "none", "Nothing on this day"));
    events.forEach(function (e) {
      var li = el("li");
      li.appendChild(el("b", "t", e.time));
      li.appendChild(document.createTextNode(e.title));
      dayList.appendChild(li);
    });
    dayChannel = state.settings.channel;
    dayDialog.hidden = false;
  }

  function closeDay() {
    dayDialog.hidden = true;
  }

  function screenById(id) {
    for (var i = 0; i < state.screens.length; i++) if (state.screens[i].id === id) return state.screens[i];
    return null;
  }

  function dropNeighbour() {
    if (drag && drag.grid.parentNode) drag.grid.parentNode.removeChild(drag.grid);
    drag = null;
    screenEl.classList.remove("slide");
    screenEl.style.transform = "";
  }

  function dragTo(dx, targetScreenId) {
    if (sliding || !state.settings) return;
    var side = neighbourSide(dx);
    var screen = side ? screenById(targetScreenId) : null;
    if (!screen) {
      if (drag) {
        dropNeighbour();
        render();
      }
      return;
    }
    if (!drag || drag.target !== screen.id || drag.side !== side) {
      dropNeighbour();
      var grid = el("div", "w95-body tv-screen next " + side);
      drag = { grid: grid, target: screen.id, side: side, key: layoutKey(screen) };
      screenEl.parentNode.appendChild(grid);
      fill(grid, screen.widgets, context());
    }
    var w = screenEl.parentNode.clientWidth;
    var x = Math.max(-w, Math.min(w, dx));
    screenEl.style.transform = slideX(x);
    drag.grid.style.transform = slideX(x);
  }

  function slideTo(px) {
    var grids = [screenEl, drag.grid];
    sliding = true;
    screenEl.getBoundingClientRect();
    return new Promise(function (resolve) {
      var done = false;
      function finish() {
        if (done) return;
        done = true;
        screenEl.removeEventListener("transitionend", onEnd);
        sliding = false;
        resolve();
      }
      function onEnd(e) {
        if (e.target === screenEl && e.propertyName === "transform") finish();
      }
      screenEl.addEventListener("transitionend", onEnd);
      setTimeout(finish, SLIDE_MS + 50);
      grids.forEach(function (g) {
        g.classList.add("slide");
        g.style.transform = slideX(px);
      });
    });
  }

  function commit() {
    if (!drag || sliding) return Promise.resolve();
    var d = drag;
    var w = screenEl.parentNode.clientWidth;
    return slideTo(d.side === "right" ? -w : w).then(function () {
      screenEl.removeEventListener("click", onClick);
      screenEl.parentNode.removeChild(screenEl);
      d.grid.classList.remove("next", "left", "right", "slide");
      d.grid.style.transform = "";
      d.grid.id = "screen";
      d.grid.addEventListener("click", onClick);
      screenEl = d.grid;
      drag = null;
      builtKey = d.key;
      loadSeq++;
      state.settings = Object.assign({}, state.settings, { screen_id: d.target });
      closeDay();
      render();
    });
  }

  function cancel() {
    if (!drag || sliding) return Promise.resolve();
    return slideTo(0).then(function () {
      dropNeighbour();
      render();
    });
  }

  function show(screenId) {
    if (!state.settings || state.settings.screen_id === screenId) return;
    loadSeq++;
    state.settings = Object.assign({}, state.settings, { screen_id: screenId });
    closeDay();
    render();
  }

  TvScreen.dragTo = dragTo;
  TvScreen.commit = commit;
  TvScreen.cancel = cancel;
  TvScreen.show = show;

  function onTouchStart(e) {
    var t = e.changedTouches[0];
    touchFrom = t ? { x: t.clientX, y: t.clientY } : null;
    lastSwipe = 0;
  }

  function onTouchEnd(e) {
    var t = e.changedTouches[0];
    if (touchFrom && t && (Math.abs(t.clientX - touchFrom.x) > TAP_SLOP_PX ||
      Math.abs(t.clientY - touchFrom.y) > TAP_SLOP_PX)) lastSwipe = Date.now();
    touchFrom = null;
  }

  function onClick(e) {
    if (Date.now() - lastSwipe < SWIPE_CLICK_MS) return;
    var sp = e.target.closest ? e.target.closest("[data-sp], [data-playlist]") : null;
    if (sp) {
      spotifyTap(sp);
      return;
    }
    var row = e.target.closest ? e.target.closest("[data-item]") : null;
    if (row) {
      setDone(Number(row.getAttribute("data-item")), !row.classList.contains("done"));
      return;
    }
    var day = e.target.closest ? e.target.closest(".tv-cal-grid .d") : null;
    if (day) {
      openDay(day.getAttribute("data-date"));
      return;
    }
    var target = e.target.closest ? e.target.closest("[data-open], [data-close]") : null;
    if (!target) return;
    var open = target.hasAttribute("data-open");
    setOpen(Number(target.getAttribute(open ? "data-open" : "data-close")), open);
  }

  document.addEventListener("touchstart", onTouchStart, { passive: true });
  document.addEventListener("touchend", onTouchEnd, { passive: true });
  screenEl.addEventListener("click", onClick);
  document.getElementById("day-close").addEventListener("click", closeDay);
  document.getElementById("day-backdrop").addEventListener("click", closeDay);
  window.addEventListener("resize", render);
  document.addEventListener("visibilitychange", syncSpotify);
  updateClock();
  setTimeout(atMidnight, msToMidnight(new Date()));
  load().then(connect);
})(typeof window !== "undefined" ? window : globalThis);
