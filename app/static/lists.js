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

  var SWIPE_PX = 60;

  function itemPatch(item, text, done, pinned) {
    var t = (text || "").trim();
    var patch = {};
    if (t !== "" && t !== item.text) patch.text = t;
    if (done !== undefined && done !== !!item.done) patch.done = done;
    if (pinned !== !!item.pinned) patch.pinned = pinned;
    return patch;
  }

  function listPatch(list, name, kind, tvOpen) {
    var n = (name || "").trim();
    var patch = {};
    if (n !== "" && n !== list.name) patch.name = n;
    if (kind && kind !== list.kind) patch.kind = kind;
    if (tvOpen !== undefined && tvOpen !== !!list.tv_open) patch.tv_open = tvOpen;
    return patch;
  }

  function swipeAction(dx, dy, onHandle) {
    if (onHandle || Math.abs(dy) >= Math.abs(dx)) return "none";
    if (dx <= -SWIPE_PX) return "delete";
    if (dx > 0) return "close";
    return "none";
  }

  function deleteText(list, screens) {
    var text = "Delete \"" + list.name + "\" and everything on it?";
    var names = [];
    (screens || []).forEach(function (s) {
      var shown = (s.widgets || []).some(function (w) { return w.list_id === list.id; });
      if (shown) names.push("\"" + s.name + "\"");
    });
    if (!names.length) return text;
    var last = names.pop();
    var joined = names.length ? names.join(", ") + " and " + last : last;
    return text + " It is also removed from the TV screen" + (names.length ? "s " : " ") + joined + ".";
  }

  var Lists = { TAB_KEY: TAB_KEY, SWIPE_PX: SWIPE_PX, rank: rank, sortItems: sortItems, pickTab: pickTab, moveId: moveId, countLabel: countLabel, itemPatch: itemPatch, listPatch: listPatch, swipeAction: swipeAction, deleteText: deleteText };
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
  var swipe = null;
  var openRow = null;
  var swallowClick = false;
  var SWIPE_OPEN_PX = 80;
  var SWIPE_SLOP_PX = 8;

  function busy() {
    return !!(editing || drag || swipe || openRow) || !$("name-dialog").hidden || !$("delete-dialog").hidden;
  }

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
    if (busy()) {
      stale = true;
      return Promise.resolve();
    }
    stale = false;
    return getJSON("/api/lists").then(function (data) {
      lists = data;
      $("lists-error").hidden = true;
      if (busy()) {
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
    var had = tabs.contains(document.activeElement) ? document.activeElement : null;
    var keep = had ? (had.id === "add-tab" ? "#add-tab" : '[data-id="' + had.getAttribute("data-id") + '"]') : null;
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
    if (keep === null) return;
    var again = tabs.querySelector(keep);
    if (again) again.focus();
  }

  function buildRow(item, notes) {
    var li = document.createElement("li");
    li.className = "item" + (item.pinned ? " pinned" : "") + (item.done ? " done" : "");
    li.setAttribute("data-id", item.id);
    li.setAttribute("data-rank", rank(item));
    var row = document.createElement("div");
    row.className = "row";
    if (!notes) {
      var box = document.createElement("label");
      box.className = "w95-check item-check";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!item.done;
      input.setAttribute("aria-label", "Done");
      box.appendChild(input);
      row.appendChild(box);
    }
    var text = document.createElement("span");
    text.className = "text";
    text.textContent = item.text;
    text.tabIndex = 0;
    text.setAttribute("role", "button");
    text.title = "Tap to edit";
    row.appendChild(text);
    var pin = document.createElement("button");
    pin.type = "button";
    pin.className = "w95-btn pin" + (item.pinned ? " on" : "");
    pin.title = item.pinned ? "Unpin" : "Pin";
    pin.setAttribute("aria-pressed", item.pinned ? "true" : "false");
    pin.textContent = "!";
    row.appendChild(pin);
    var handle = document.createElement("span");
    handle.className = "drag";
    handle.title = "Drag to reorder";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "::";
    row.appendChild(handle);
    var del = document.createElement("button");
    del.type = "button";
    del.className = "w95-btn swipe-delete";
    del.tabIndex = -1;
    del.textContent = "Delete";
    li.appendChild(del);
    li.appendChild(row);
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
    $("clear-done").hidden = !list.items.some(function (it) { return it.done; });
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
    if (b.id === "add-tab") openName();
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
    if (e.target.closest(".swipe-delete")) {
      closeSwipe();
      openItem(item, true);
    } else if (e.target.closest(".pin")) {
      item.pinned = !item.pinned;
      renderPanel(currentList());
      patchItem(item.id, { pinned: item.pinned }, "Could not pin the item. Try again.");
    } else if (e.target.closest(".text")) {
      openItem(item, false);
    }
  }

  function onItemsKey(e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("text")) {
      e.preventDefault();
      var item = findItem(rowId(e.target.closest(".item")));
      if (item) openItem(item, false);
    }
  }

  function rowFor(id) {
    return $("items").querySelector('.item[data-id="' + id + '"]');
  }

  function showItemPart(name) {
    $("item-edit").hidden = name !== "edit";
    $("item-confirm").hidden = name !== "confirm";
  }

  function openItem(item, confirmOnly) {
    if (editing) return;
    var list = currentList();
    editing = { id: item.id, confirmOnly: confirmOnly };
    $("item-text").value = item.text;
    $("item-done").checked = !!item.done;
    $("item-done-row").hidden = !!list && list.kind === "notes";
    $("item-pinned").checked = !!item.pinned;
    showItemPart(confirmOnly ? "confirm" : "edit");
    $("item-dialog").hidden = false;
    if (confirmOnly) $("item-confirm-delete").focus();
    else $("item-text").focus();
  }

  function closeItem() {
    var ed = editing;
    $("item-dialog").hidden = true;
    editing = null;
    var li = ed && rowFor(ed.id);
    if (li) li.querySelector(".text").focus();
    afterIdle();
  }

  function saveItem(e) {
    e.preventDefault();
    var ed = editing;
    var item = ed && findItem(ed.id);
    if (!item) {
      closeItem();
      return;
    }
    var text = $("item-text").value;
    if (!text.trim()) {
      $("item-text").focus();
      return;
    }
    var done = $("item-done-row").hidden ? undefined : $("item-done").checked;
    var patch = itemPatch(item, text, done, $("item-pinned").checked);
    if (!Object.keys(patch).length) {
      closeItem();
      return;
    }
    for (var k in patch) item[k] = patch[k];
    renderPanel(currentList());
    closeItem();
    patchItem(item.id, patch, "Could not save the item. Try again.");
  }

  function askDeleteItem() {
    showItemPart("confirm");
    $("item-confirm-delete").focus();
  }

  function cancelDeleteItem() {
    if (editing && editing.confirmOnly) {
      closeItem();
      return;
    }
    showItemPart("edit");
    $("item-delete").focus();
  }

  function deleteItem() {
    var ed = editing;
    var list = currentList();
    closeItem();
    if (!ed || !list) return;
    list.items = list.items.filter(function (it) { return it.id !== ed.id; });
    renderPanel(list);
    send("DELETE", "/api/items/" + ed.id).then(load, fail("Could not delete the item. Try again."));
  }

  function setOffset(li, x) {
    li.querySelector(".row").style.transform = x ? "translateX(" + x + "px)" : "";
  }

  function closeSwipe() {
    if (!openRow) return;
    var li = openRow;
    openRow = null;
    li.classList.remove("open");
    setOffset(li, 0);
    afterIdle();
  }

  function onDocPointerDown(e) {
    swallowClick = false;
    if (!openRow || openRow.contains(e.target)) return;
    closeSwipe();
    if ($("items").contains(e.target)) swallowClick = true;
  }

  function onDocClick(e) {
    if (!swallowClick) return;
    swallowClick = false;
    e.preventDefault();
    e.stopPropagation();
  }

  function onSwipeDown(e) {
    if (e.pointerType !== "touch" || swipe || drag || editing) return;
    var li = e.target.closest(".item");
    if (!li || e.target.closest(".drag") || e.target.closest(".swipe-delete")) return;
    swipe = { li: li, id: e.pointerId, x: e.clientX, y: e.clientY, base: li === openRow ? -SWIPE_OPEN_PX : 0, locked: false };
    window.addEventListener("pointermove", onSwipeMove);
    window.addEventListener("pointerup", onSwipeUp);
    window.addEventListener("pointercancel", onSwipeUp);
  }

  function stopSwipe() {
    window.removeEventListener("pointermove", onSwipeMove);
    window.removeEventListener("pointerup", onSwipeUp);
    window.removeEventListener("pointercancel", onSwipeUp);
    var s = swipe;
    swipe = null;
    if (s) s.li.classList.remove("swiping");
    return s;
  }

  function onSwipeMove(e) {
    if (!swipe || e.pointerId !== swipe.id) return;
    var dx = e.clientX - swipe.x;
    var dy = e.clientY - swipe.y;
    if (!swipe.locked) {
      if (Math.abs(dx) < SWIPE_SLOP_PX && Math.abs(dy) < SWIPE_SLOP_PX) return;
      if (Math.abs(dy) >= Math.abs(dx)) {
        stopSwipe();
        afterIdle();
        return;
      }
      swipe.locked = true;
      swipe.li.classList.add("swiping");
    }
    setOffset(swipe.li, Math.max(-SWIPE_OPEN_PX, Math.min(0, swipe.base + dx)));
  }

  function onSwipeUp(e) {
    if (!swipe || e.pointerId !== swipe.id) return;
    var s = stopSwipe();
    if (!s.locked) {
      if (s.li === openRow) {
        swallowClick = true;
        closeSwipe();
      } else {
        afterIdle();
      }
      return;
    }
    swallowClick = true;
    var action = e.type === "pointercancel" ? "none" : swipeAction(e.clientX - s.x, e.clientY - s.y, false);
    var open = s.base ? action !== "close" : action === "delete";
    if (open) {
      if (openRow && openRow !== s.li) closeSwipe();
      openRow = s.li;
      s.li.classList.add("open");
      setOffset(s.li, -SWIPE_OPEN_PX);
    } else {
      openRow = s.li;
      closeSwipe();
    }
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

  function openOptions() {
    var list = currentList();
    if (!list || editing) return;
    editing = { list: list.id };
    $("list-title").textContent = list.name;
    $("list-name").value = list.name;
    var radios = document.querySelectorAll('input[name="kind"]');
    for (var i = 0; i < radios.length; i++) radios[i].checked = radios[i].value === list.kind;
    $("list-tv-open").checked = !!list.tv_open;
    $("list-dialog").hidden = false;
    $("list-name").focus();
  }

  function closeOptions() {
    $("list-dialog").hidden = true;
    editing = null;
    $("list-options").focus();
    afterIdle();
  }

  function saveOptions(e) {
    e.preventDefault();
    var ed = editing;
    var list = currentList();
    if (!ed || !list || list.id !== ed.list) {
      closeOptions();
      return;
    }
    if (!$("list-name").value.trim()) {
      $("list-name").focus();
      return;
    }
    var kind = document.querySelector('input[name="kind"]:checked');
    var patch = listPatch(list, $("list-name").value, kind && kind.value, $("list-tv-open").checked);
    closeOptions();
    if (!Object.keys(patch).length) return;
    send("PATCH", "/api/lists/" + list.id, patch).then(load, fail("Could not save the list. Try again."));
  }

  function deleteFromOptions() {
    if (!currentList()) return;
    $("list-dialog").hidden = true;
    openDelete();
  }

  function clearDone() {
    var list = currentList();
    if (!list) return;
    send("POST", "/api/lists/" + list.id + "/clear-done").then(load, fail("Could not clear the done items. Try again."));
  }

  function openName() {
    $("name-input").value = "";
    $("name-dialog").hidden = false;
    $("name-input").focus();
  }

  function closeName() {
    $("name-dialog").hidden = true;
    var add = $("tabs").querySelector(".add");
    if (add) add.focus();
    afterIdle();
  }

  function saveName(e) {
    e.preventDefault();
    var name = $("name-input").value.trim();
    if (!name) {
      $("name-input").focus();
      return;
    }
    closeName();
    send("POST", "/api/lists", { name: name }).then(function (r) {
      return r.json();
    }).then(function (created) {
      selected = created.id;
      saveTab(created.id);
      return load();
    }, function () {
      showError("Could not make the list. Try again.");
    });
  }

  function openDelete() {
    var list = currentList();
    if (!list) return;
    $("delete-text").textContent = deleteText(list, []);
    $("delete-dialog").hidden = false;
    $("confirm-delete").focus();
    getJSON("/api/screens").then(function (screens) {
      if ($("delete-dialog").hidden || currentList() !== list) return;
      $("delete-text").textContent = deleteText(list, screens);
    }, function () {});
  }

  function closeDelete() {
    $("delete-dialog").hidden = true;
    if (editing && editing.list) {
      $("list-dialog").hidden = false;
      $("delete-list").focus();
      return;
    }
    $("list-options").focus();
    afterIdle();
  }

  function deleteList() {
    var list = currentList();
    $("delete-dialog").hidden = true;
    editing = null;
    $("list-options").focus();
    afterIdle();
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
    ul.addEventListener("pointerdown", onSwipeDown);
    document.addEventListener("pointerdown", onDocPointerDown, true);
    document.addEventListener("click", onDocClick, true);
    $("item-edit").addEventListener("submit", saveItem);
    $("item-cancel").addEventListener("click", closeItem);
    $("item-close").addEventListener("click", closeItem);
    $("item-backdrop").addEventListener("click", closeItem);
    $("item-delete").addEventListener("click", askDeleteItem);
    $("item-confirm-delete").addEventListener("click", deleteItem);
    $("item-confirm-cancel").addEventListener("click", cancelDeleteItem);
    $("clear-done").addEventListener("click", clearDone);
    $("list-options").addEventListener("click", openOptions);
    $("list-form").addEventListener("submit", saveOptions);
    $("list-cancel").addEventListener("click", closeOptions);
    $("list-close").addEventListener("click", closeOptions);
    $("list-backdrop").addEventListener("click", closeOptions);
    $("delete-list").addEventListener("click", deleteFromOptions);
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
      if (!$("item-dialog").hidden) closeItem();
      else if (!$("name-dialog").hidden) closeName();
      else if (!$("list-dialog").hidden) closeOptions();
      else if (!$("delete-dialog").hidden) closeDelete();
    });

    load();
    loadStatus();
    setInterval(loadStatus, STATUS_MS);
    connectEvents();
  }

  init();
})(typeof window !== "undefined" ? window : globalThis);
