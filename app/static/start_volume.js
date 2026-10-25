(function () {
  "use strict";

  var POLL_MS = 3000;
  var SEND_MS = 250;
  var HOLD_MS = 2500;
  var STEP = 5;

  var group = document.getElementById("volume-group");
  var slider = document.getElementById("volume-slider");
  var value = document.getElementById("volume-value");
  var timer = null;
  var sendTimer = null;
  var touchedAt = 0;
  var seq = 0;

  function show(percent) {
    slider.value = String(percent);
    value.textContent = percent + "%";
  }

  function apply(now) {
    var playing = !!(now && now.linked !== false && now.playing && now.track);
    group.hidden = !playing;
    if (playing && typeof now.volume === "number" && Date.now() - touchedAt > HOLD_MS) show(now.volume);
  }

  function poll() {
    clearTimeout(timer);
    timer = null;
    if (document.visibilityState === "hidden") return;
    var mine = ++seq;
    fetch("/api/spotify/now", { cache: "no-store" }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (now) {
      if (mine === seq) apply(now);
    }, function () {
      if (mine === seq) apply(null);
    }).then(function () {
      if (mine === seq && document.visibilityState !== "hidden") timer = setTimeout(poll, POLL_MS);
    });
  }

  function send() {
    clearTimeout(sendTimer);
    sendTimer = null;
    touchedAt = Date.now();
    seq++;
    fetch("/api/spotify/volume", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ percent: Number(slider.value) })
    }).catch(function () {}).then(function () {
      touchedAt = Date.now();
      clearTimeout(timer);
      timer = setTimeout(poll, POLL_MS);
    });
  }

  function step(delta) {
    show(Math.max(0, Math.min(100, Number(slider.value) + delta)));
    send();
  }

  slider.addEventListener("input", function () {
    touchedAt = Date.now();
    value.textContent = slider.value + "%";
    clearTimeout(sendTimer);
    sendTimer = setTimeout(send, SEND_MS);
  });
  slider.addEventListener("change", send);
  document.getElementById("volume-down").addEventListener("click", function () { step(-STEP); });
  document.getElementById("volume-up").addEventListener("click", function () { step(STEP); });
  document.addEventListener("visibilitychange", poll);

  poll();
})();
