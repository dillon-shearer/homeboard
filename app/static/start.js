(function () {
  "use strict";

  var STATUS_MS = 10000;

  function loadStatus() {
    fetch("/api/status", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (s) {
      var n = s.photo_count;
      document.getElementById("photo-count").textContent = n + (n === 1 ? " photo" : " photos");
      document.getElementById("display-state").textContent = s.display_connected ? "Display online" : "Display offline";
    }).catch(function () {
      document.getElementById("display-state").textContent = "Server not reachable";
    });
  }

  loadStatus();
  setInterval(loadStatus, STATUS_MS);
})();
