(function () {
  "use strict";

  var NOW_MS = 5000;
  var NO_CREDS = "Spotify is not set up on the Pi yet. See PI_SETUP.md, Spotify.";

  var linked = null;
  var displayName = null;

  function $(id) {
    return document.getElementById(id);
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (out) {
        if (!r.ok) {
          var err = new Error("HTTP " + r.status);
          err.status = r.status;
          throw err;
        }
        return out;
      });
    });
  }

  function renderSpotify(s) {
    var was = linked;
    if (s.linked) {
      linked = true;
      if (s.display_name) displayName = s.display_name;
      $("spotify-state").textContent = displayName ? "Linked as " + displayName : "Linked to Spotify";
    } else if (s.reason === "no credentials") {
      linked = false;
      $("spotify-state").textContent = NO_CREDS;
    } else {
      linked = false;
      $("spotify-state").textContent = "Not linked";
    }
    if (!linked) displayName = null;
    if (was !== linked && linked) loadProfile();
  }

  function loadProfile() {
    return getJSON("/api/spotify/me").then(function (p) {
      if (!linked || !p.display_name) return;
      displayName = p.display_name;
      $("spotify-state").textContent = "Linked as " + displayName;
    }).catch(function () {});
  }

  function loadNow() {
    return getJSON("/api/spotify/now").then(renderSpotify).catch(function () {
      if (linked !== null) return;
      $("spotify-state").textContent = "Spotify not reachable";
    });
  }

  function renderSpeaker(s) {
    var sp = (s && s.speaker) || {};
    $("speaker-state").textContent = sp.connected ? "Connected to " + (sp.name || "the speaker") : "Not connected";
  }

  function speakerError(err) {
    $("speaker-state").textContent = err.status === 503 ? "Helper not running" : "Speaker status unknown";
  }

  function loadSpeaker() {
    return getJSON("/api/sound/status").then(renderSpeaker).catch(speakerError);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    loadNow();
    loadSpeaker();
  });
  setInterval(function () {
    if (document.visibilityState === "visible") loadNow();
  }, NOW_MS);

  loadSpeaker();
  loadNow();
})();
