(function () {
  "use strict";

  function $(id) {
    return document.getElementById(id);
  }

  fetch("/api/status", { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }).then(function (s) {
    $("host").textContent = s.hostname;
    $("ip").textContent = s.ip || "";
    $("disk").textContent = (s.disk_free_bytes / 1073741824).toFixed(1) + " GB free";
    $("version").textContent = "v" + s.version;
  }).catch(function () {
    $("host").textContent = "Server not reachable";
  });
})();
