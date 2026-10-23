(function (root) {
  "use strict";

  var TAB_KEY = "homeboard.lists.tab";

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

  function pickTab(lists, saved) {
    if (!lists.length) return null;
    var id = Number(saved);
    for (var i = 0; i < lists.length; i++) if (lists[i].id === id) return id;
    return lists[0].id;
  }

  function moveId(ids, from, to) {
    var out = ids.slice();
    if (from < 0 || from >= out.length || to < 0 || to >= out.length) return out;
    var item = out.splice(from, 1)[0];
    out.splice(to, 0, item);
    return out;
  }

  function countLabel(n) {
    return n + (n === 1 ? " list" : " lists");
  }

  var Lists = { TAB_KEY: TAB_KEY, rank: rank, sortItems: sortItems, pickTab: pickTab, moveId: moveId, countLabel: countLabel };
  root.Lists = Lists;
  if (typeof module !== "undefined" && module.exports) module.exports = Lists;
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

  function readTab() {
    try {
      return root.localStorage.getItem(TAB_KEY);
    } catch (e) {
      return null;
    }
  }

  function saveTab(id) {
    try {
      root.localStorage.setItem(TAB_KEY, String(id));
    } catch (e) {
      return;
    }
  }

  var lists = [];
  var selected = null;
  var stale = false;
  var editing = null;
  var drag = null;

  function currentList() {
    for (var i = 0; i < lists.length; i++) if (lists[i].id === selected) return lists[i];
    return null;
  }

  function loadStatus() {
    return getJSON("/api/status").then(function (s) {
      $("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
    }).catch(function () {
      $("display-state").textContent = "Server not reachable";
    });
  }

  function load() {
    if (editing || drag) {
      stale = true;
      return Promise.resolve();
    }
    stale = false;
    return getJSON("/api/lists").then(function (data) {
      lists = data;
      $("lists-error").hidden = true;
      if (editing || drag) {
        stale = true;
        return;
      }
      render();
    }).catch(function () {
      $("panel").hidden = true;
      $("lists-empty").hidden = true;
      $("lists-error").hidden = false;
    });
  }

  function afterIdle() {
    if (stale) load();
  }

  function render() {
    selected = pickTab(lists, selected == null ? readTab() : selected);
    $("list-count").textContent = countLabel(lists.length);
    renderTabs();
    var list = currentList();
    $("panel").hidden = !list;
    $("lists-empty").hidden = !!list;
    if (list) renderPanel(list);
  }

  function renderTabs() {
    var tabs = $("tabs");
    tabs.textContent = "";
    lists.forEach(function (l) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "tab" + (l.id === selected ? " on" : "");
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", l.id === selected ? "true" : "false");
      b.setAttribute("data-id", l.id);
      b.textContent = l.name;
      tabs.appendChild(b);
    });
    var add = document.createElement("button");
    add.type = "button";
    add.id = "add-tab";
    add.className = "tab add";
    add.title = "New list";
    add.setAttribute("aria-label", "New list");
    add.textContent = "+";
    tabs.appendChild(add);
  }

  function buildRow(item, notes) {
    var li = document.createElement("li");
    li.className = "item" + (item.pinned ? " pinned" : "") + (item.done ? " done" : "");
    li.setAttribute("data-id", item.id);
    li.setAttribute("data-rank", rank(item));
    if (!notes) {
      var box = document.createElement("label");
      box.className = "w95-check item-check";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!item.done;
      input.setAttribute("aria-label", "Done");
      box.appendChild(input);
      li.appendChild(box);
    }
    var text = document.createElement("span");
    text.className = "text";
    text.textContent = item.text;
    text.tabIndex = 0;
    text.setAttribute("role", "button");
    text.title = "Tap to edit";
    li.appendChild(text);
    var pin = document.createElement("button");
    pin.type = "button";
    pin.className = "w95-btn pin" + (item.pinned ? " on" : "");
    pin.title = item.pinned ? "Unpin" : "Pin";
    pin.setAttribute("aria-pressed", item.pinned ? "true" : "false");
    pin.textContent = "!";
    li.appendChild(pin);
    var handle = document.createElement("span");
    handle.className = "drag";
    handle.title = "Drag to reorder";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "::";
    li.appendChild(handle);
    return li;
  }

  function renderPanel(list) {
    var ul = $("items");
    ul.textContent = "";
    var notes = list.kind === "notes";
    ul.classList.toggle("notes", notes);
    sortItems(list.items).forEach(function (item) {
      ul.appendChild(buildRow(item, notes));
    });
    $("items-empty").hidden = list.items.length > 0;
    var radios = document.querySelectorAll('input[name="kind"]');
    for (var i = 0; i < radios.length; i++) radios[i].checked = radios[i].value === list.kind;
    $("show-on-tv").checked = !!list.show_on_tv;
    $("clear-done").disabled = !list.items.some(function (it) { return it.done; });
    $("panel").setAttribute("aria-label", list.name);
  }

  function findItem(id) {
    var list = currentList();
    if (!list) return null;
    for (var i = 0; i < list.items.length; i++) if (list.items[i].id === id) return list.items[i];
    return null;
  }

  function rowId(li) {
    return Number(li.getAttribute("data-id"));
  }

  function fail(msg) {
    return function () {
      showError(msg);
      load();
    };
  }

  function selectTab(id) {
    selected = id;
    saveTab(id);
    render();
  }

  function onTabsClick(e) {
    var b = e.target.closest(".tab");
    if (!b) return;
    if (b.id === "add-tab") openName("new");
    else selectTab(Number(b.getAttribute("data-id")));
  }

  function addItem(e) {
    e.preventDefault();
    var list = currentList();
    var input = $("item-input");
    var text = input.value.trim();
    if (!list || !text) return;
    input.value = "";
    send("POST", "/api/lists/" + list.id + "/items", { text: text }).then(load, function () {
      input.value = text;
      showError("Could not add the item. Try again.");
    });
    input.focus();
  }

  function patchItem(id, patch, msg) {
    return send("PATCH", "/api/items/" + id, patch).then(load, fail(msg));
  }

  function onItemsChange(e) {
    if (e.target.type !== "checkbox") return;
    var li = e.target.closest(".item");
    var item = findItem(rowId(li));
    if (!item) return;
    item.done = e.target.checked;
    renderPanel(currentList());
    patchItem(item.id, { done: item.done }, "Could not save the item. Try again.");
  }

  function onItemsClick(e) {
    var li = e.target.closest(".item");
    if (!li) return;
    var item = findItem(rowId(li));
    if (!item) return;
    if (e.target.closest(".pin")) {
      item.pinned = !item.pinned;
      renderPanel(currentList());
      patchItem(item.id, { pinned: item.pinned }, "Could not pin the item. Try again.");
    } else if (e.target.closest(".text")) {
      startEdit(li, item);
    }
  }

  function onItemsKey(e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("text")) {
      e.preventDefault();
      var li = e.target.closest(".item");
      var item = findItem(rowId(li));
      if (item) startEdit(li, item);
    }
  }

  function startEdit(li, item) {
    if (editing) return;
    var span = li.querySelector(".text");
    var input = document.createElement("input");
    input.type = "text";
    input.className = "w95-input text-edit";
    input.maxLength = 200;
    input.value = item.text;
    input.setAttribute("aria-label", "Item text");
    input.setAttribute("enterkeyhint", "done");
    editing = { li: li, item: item, input: input, span: span };
    li.replaceChild(input, span);
    input.focus();
    input.select();
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        finishEdit(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        finishEdit(false);
      }
    });
    input.addEventListener("blur", function () {
      finishEdit(true);
    });
  }

  function finishEdit(save) {
    var ed = editing;
    if (!ed) return;
    editing = null;
    var text = ed.input.value.trim();
    if (ed.input.parentNode) ed.li.replaceChild(ed.span, ed.input);
    if (!save || text === ed.item.text) {
      afterIdle();
      return;
    }
    if (text === "") {
      send("DELETE", "/api/items/" + ed.item.id).then(load, fail("Could not delete the item. Try again."));
      return;
    }
    ed.span.textContent = text;
    patchItem(ed.item.id, { text: text }, "Could not save the item. Try again.");
  }

  function rowIds() {
    var rows = $("items").querySelectorAll(".item");
    var ids = [];
    for (var i = 0; i < rows.length; i++) ids.push(rowId(rows[i]));
    return ids;
  }

  function applyOrder(ids) {
    var ul = $("items");
    ids.forEach(function (id) {
      var li = ul.querySelector('.item[data-id="' + id + '"]');
      if (li) ul.appendChild(li);
    });
  }

  function onPointerDown(e) {
    var handle = e.target.closest(".drag");
    if (!handle || drag || editing) return;
    e.preventDefault();
    var li = handle.closest(".item");
    drag = { li: li, start: rowIds() };
    li.classList.add("dragging");
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    if (!drag) return;
    e.preventDefault();
    var el = document.elementFromPoint(e.clientX, e.clientY);
    var target = el && el.closest("#items .item");
    if (!target || target === drag.li) return;
    if (target.getAttribute("data-rank") !== drag.li.getAttribute("data-rank")) return;
    var ids = rowIds();
    applyOrder(moveId(ids, ids.indexOf(rowId(drag.li)), ids.indexOf(rowId(target))));
  }

  function onPointerUp() {
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);
    if (!drag) return;
    var d = drag;
    drag = null;
    d.li.classList.remove("dragging");
    var ids = rowIds();
    var list = currentList();
    if (!list || ids.join(",") === d.start.join(",")) {
      afterIdle();
      return;
    }
    send("PUT", "/api/lists/" + list.id + "/items/order", { ids: ids }).then(load, function () {
      applyOrder(d.start);
      showError("Could not save the new order. Try again.");
      afterIdle();
    });
  }

  function patchList(patch, msg) {
    var list = currentList();
    if (!list) return Promise.resolve();
    return send("PATCH", "/api/lists/" + list.id, patch).then(load, fail(msg));
  }

  function onKindChange(e) {
    if (!e.currentTarget.checked) return;
    var list = currentList();
    if (!list) return;
    list.kind = e.currentTarget.value;
    renderPanel(list);
    patchList({ kind: list.kind }, "Could not change the kind. Try again.");
  }

  function onTvChange() {
    patchList({ show_on_tv: $("show-on-tv").checked }, "Could not save the TV setting. Try again.");
  }

  function clearDone() {
    var list = currentList();
    if (!list) return;
    send("POST", "/api/lists/" + list.id + "/clear-done").then(load, fail("Could not clear the done items. Try again."));
  }

  var nameMode = null;

  function openName(mode) {
    nameMode = mode;
    var list = currentList();
    $("name-title").textContent = mode === "new" ? "New list" : "Rename list";
    $("name-input").value = mode === "rename" && list ? list.name : "";
    $("name-dialog").hidden = false;
    $("name-input").focus();
  }

  function closeName() {
    $("name-dialog").hidden = true;
    nameMode = null;
  }

  function saveName(e) {
    e.preventDefault();
    var name = $("name-input").value.trim();
    if (!name) {
      $("name-input").focus();
      return;
    }
    var mode = nameMode;
    closeName();
    if (mode === "new") {
      send("POST", "/api/lists", { name: name }).then(function (r) {
        return r.json();
      }).then(function (created) {
        selected = created.id;
        saveTab(created.id);
        return load();
      }, function () {
        showError("Could not make the list. Try again.");
      });
    } else if (mode === "rename") {
      patchList({ name: name }, "Could not rename the list. Try again.");
    }
  }

  function openDelete() {
    var list = currentList();
    if (!list) return;
    $("delete-text").textContent = "Delete \"" + list.name + "\" and everything on it?";
    $("delete-dialog").hidden = false;
    $("confirm-delete").focus();
  }

  function closeDelete() {
    $("delete-dialog").hidden = true;
  }

  function deleteList() {
    var list = currentList();
    closeDelete();
    if (!list) return;
    send("DELETE", "/api/lists/" + list.id).then(function () {
      selected = null;
      return load();
    }, fail("Could not delete the list. Try again."));
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
    es.addEventListener("lists_changed", load);
    es.onerror = function () {
      es.close();
      retryEvents();
    };
  }

  function init() {
    $("tabs").addEventListener("click", onTabsClick);
    $("add-form").addEventListener("submit", addItem);
    var ul = $("items");
    ul.addEventListener("change", onItemsChange);
    ul.addEventListener("click", onItemsClick);
    ul.addEventListener("keydown", onItemsKey);
    ul.addEventListener("pointerdown", onPointerDown);
    var radios = document.querySelectorAll('input[name="kind"]');
    for (var i = 0; i < radios.length; i++) radios[i].addEventListener("change", onKindChange);
    $("show-on-tv").addEventListener("change", onTvChange);
    $("clear-done").addEventListener("click", clearDone);
    $("rename-list").addEventListener("click", function () { openName("rename"); });
    $("delete-list").addEventListener("click", openDelete);
    $("name-form").addEventListener("submit", saveName);
    $("name-cancel").addEventListener("click", closeName);
    $("name-close").addEventListener("click", closeName);
    $("name-backdrop").addEventListener("click", closeName);
    $("confirm-delete").addEventListener("click", deleteList);
    $("confirm-cancel").addEventListener("click", closeDelete);
    $("delete-close").addEventListener("click", closeDelete);
    $("delete-backdrop").addEventListener("click", closeDelete);
    $("lists-retry").addEventListener("click", load);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("name-dialog").hidden) closeName();
      else if (!$("delete-dialog").hidden) closeDelete();
    });

    load();
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
    connectEvents();
  }

  init();
})(typeof window !== "undefined" ? window : globalThis);
