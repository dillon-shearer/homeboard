(function (root) {
  "use strict";

  var MAX_BACKOFF_S = 30;
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];

  function rank(item) {
    if (item.done) return 2;
    return item.pinned ? 0 : 1;
  }

  function sortItems(items) {
    return items.map(function (item, i) {
      return { item: item, i: i };
    }).sort(function (a, b) {
      return rank(a.item) - rank(b.item) || (a.item.sort_order || 0) - (b.item.sort_order || 0) || a.i - b.i;
    }).map(function (x) {
      return x.item;
    });
  }

  function currentScreen(screens, screenId) {
    var sorted = (screens || []).slice().sort(function (a, b) {
      return (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id;
    });
    for (var i = 0; i < sorted.length; i++) if (sorted[i].id === screenId) return sorted[i];
    return sorted[0] || null;
  }

  function visibleLists(lists, screens, screenId) {
    var screen = currentScreen(screens, screenId);
    var out = [];
    if (!screen) return out;
    (lists || []).forEach(function (list) {
      if (list.screen_id !== undefined && list.screen_id !== screen.id) return;
      var items = sortItems(list.items || []);
      if (!items.length) return;
      out.push(Object.assign({}, list, { items: items }));
    });
    return out;
  }

  function moreLabel(hidden, done) {
    return "+" + hidden + " more" + (done ? ", " + done + " done" : "");
  }

  function formatClock(d) {
    var h = d.getHours() % 12 || 12;
    var m = String(d.getMinutes()).padStart(2, "0");
    return h + ":" + m + " " + (d.getHours() < 12 ? "am" : "pm");
  }

  function formatDate(d) {
    return DAYS[d.getDay()] + ", " + MONTHS[d.getMonth()] + " " + d.getDate();
  }

  function countLabel(n) {
    return n + (n === 1 ? " list" : " lists");
  }

  var TvLists = {
    sortItems: sortItems,
    currentScreen: currentScreen,
    visibleLists: visibleLists,
    moreLabel: moreLabel,
    formatClock: formatClock,
    formatDate: formatDate,
    countLabel: countLabel,
  };

  root.TvLists = TvLists;
  if (typeof module !== "undefined" && module.exports) module.exports = TvLists;
  if (typeof document === "undefined") return;

  var cardsEl = document.getElementById("cards");
  var clockEl = document.getElementById("clock");
  var countEl = document.getElementById("list-count");
  var titleEl = document.getElementById("title");
  var todayEl = document.getElementById("today");
  var backoff = 1;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function buildCard(list) {
    var notes = list.kind === "notes";
    var card = el("section", notes ? "tv-card notes" : "tv-card");
    card.appendChild(el("h2", "", list.name));
    var ul = el("ul");
    list.items.forEach(function (item) {
      var li = el("li", item.done ? "done" : item.pinned ? "pin" : "");
      if (!notes && (item.done || !item.pinned)) li.appendChild(el("span", item.done ? "box tick" : "box"));
      li.appendChild(document.createTextNode(item.text));
      ul.appendChild(li);
    });
    card.appendChild(ul);
    return card;
  }

  function fitCard(card) {
    var ul = card.querySelector("ul");
    if (!ul) return;
    var old = card.querySelector(".more");
    if (old) card.removeChild(old);
    var rows = Array.prototype.slice.call(ul.children);
    rows.forEach(function (li) { li.hidden = false; });
    var more = null;
    var hidden = 0;
    var done = 0;
    for (var i = rows.length - 1; i >= 0 && ul.scrollHeight > ul.clientHeight; i--) {
      if (rows[i].classList.contains("pin")) continue;
      rows[i].hidden = true;
      hidden++;
      if (rows[i].classList.contains("done")) done++;
      if (!more) more = card.appendChild(el("div", "more"));
      more.textContent = moreLabel(hidden, done);
    }
  }

  function fitAll() {
    Array.prototype.forEach.call(cardsEl.querySelectorAll(".tv-card"), fitCard);
  }

  function render(lists, screens, settings) {
    var screenId = settings ? settings.screen_id : null;
    var screen = currentScreen(screens, screenId);
    var shown = visibleLists(lists, screens, screenId);
    titleEl.textContent = screen ? screen.name : "Lists";
    cardsEl.textContent = "";
    if (!shown.length) {
      cardsEl.appendChild(el("section", "tv-card empty", "Nothing on this screen"));
    } else {
      shown.forEach(function (list) {
        cardsEl.appendChild(buildCard(list));
      });
    }
    countEl.textContent = countLabel(shown.length);
    fitAll();
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }

  function load() {
    return Promise.all([getJSON("/api/lists"), getJSON("/api/screens"), getJSON("/api/settings")]).then(function (res) {
      render(res[0], res[1], res[2]);
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
    es.addEventListener("lists_changed", load);
    es.addEventListener("settings_changed", load);
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
    setTimeout(updateClock, 60000 - now.getSeconds() * 1000 - now.getMilliseconds() + 20);
  }

  window.addEventListener("resize", fitAll);
  updateClock();
  load().then(connect);
})(typeof window !== "undefined" ? window : globalThis);
