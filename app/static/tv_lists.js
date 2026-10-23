(function (root) {
  "use strict";

  var MAX_ITEMS = 8;
  var MAX_BACKOFF_S = 30;
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];

  function openItems(items) {
    return items.filter(function (item) {
      return !item.done;
    }).map(function (item, i) {
      return { item: item, i: i };
    }).sort(function (a, b) {
      return (b.item.pinned ? 1 : 0) - (a.item.pinned ? 1 : 0) ||
        (a.item.sort_order || 0) - (b.item.sort_order || 0) || a.i - b.i;
    }).map(function (x) {
      return x.item;
    });
  }

  function visibleLists(lists) {
    var out = [];
    (lists || []).forEach(function (list) {
      if (!list.show_on_tv) return;
      var items = openItems(list.items || []);
      if (!items.length) return;
      out.push(Object.assign({}, list, { items: items }));
    });
    return out;
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
    MAX_ITEMS: MAX_ITEMS,
    openItems: openItems,
    visibleLists: visibleLists,
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
    list.items.slice(0, MAX_ITEMS).forEach(function (item) {
      var li = el("li", item.pinned ? "pin" : "");
      if (!notes && !item.pinned) li.appendChild(el("span", "box"));
      li.appendChild(document.createTextNode(item.text));
      ul.appendChild(li);
    });
    card.appendChild(ul);
    var hidden = list.items.length - MAX_ITEMS;
    if (hidden > 0) card.appendChild(el("div", "more", "+" + hidden + " more"));
    return card;
  }

  function render(lists) {
    var shown = visibleLists(lists);
    cardsEl.textContent = "";
    if (!shown.length) {
      cardsEl.appendChild(el("section", "tv-card empty", "Nothing on the lists"));
    } else {
      shown.forEach(function (list) {
        cardsEl.appendChild(buildCard(list));
      });
    }
    countEl.textContent = countLabel(shown.length);
  }

  function load() {
    return fetch("/api/lists", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(render, function () {});
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

  updateClock();
  load().then(connect);
})(typeof window !== "undefined" ? window : globalThis);
