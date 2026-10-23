(function () {
  "use strict";

  var STATUS_MS = 10000;

  function showChannel(channel) {
    var buttons = document.querySelectorAll("[data-channel]");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].getAttribute("data-channel") === channel;
      buttons[i].classList.toggle("on", on);
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function putSettings(body) {
    return fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      showChannel(s.channel);
      document.getElementById("channel-timeout").value = String(s.channel_timeout_min);
    }).catch(function () {
      document.getElementById("display-state").textContent = "Server not reachable";
    });
  }

  function loadSettings() {
    fetch("/api/settings", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      showChannel(s.channel);
      document.getElementById("channel-timeout").value = String(s.channel_timeout_min);
    }).catch(function () {});
  }

  function loadStatus() {
    fetch("/api/status", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      var n = s.photo_count;
      document.getElementById("photo-count").textContent = n + (n === 1 ? " photo" : " photos");
      document.getElementById("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
      if (s.channel) showChannel(s.channel);
    }).catch(function () {
      document.getElementById("display-state").textContent = "Server not reachable";
    });
  }

  var buttons = document.querySelectorAll("[data-channel]");
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].addEventListener("click", function (e) {
      var channel = e.currentTarget.getAttribute("data-channel");
      showChannel(channel);
      putSettings({ channel: channel });
    });
  }
  document.getElementById("channel-timeout").addEventListener("change", function (e) {
    putSettings({ channel_timeout_min: parseInt(e.target.value, 10) });
  });

  loadSettings();
  loadStatus();
  setInterval(loadStatus, STATUS_MS);
})();
