(function (root) {
  "use strict";

  var MAX_BACKOFF_S = 30;
  var CHAIN_MS = 1000;
  var HOLD_DELAY_MS = 400;
  var HOLD_REPEAT_MS = 80;
  var SPOTIFY_DOWN_TEXT = "Could not reach Spotify";

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) {
        var err = new Error("HTTP " + r.status);
        err.status = r.status;
        throw err;
      }
      return r.json();
    });
  }

  function send(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body === undefined ? {} : body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (out) {
        if (!r.ok) {
          var err = new Error(typeof out.detail === "string" ? out.detail : "Could not save");
          err.status = r.status;
          throw err;
        }
        return out;
      });
    });
  }

  function errorText(r, fallback) {
    return r.json().catch(function () { return {}; }).then(function (out) {
      return (r.status === 409 || r.status === 429) && typeof out.detail === "string" ? out.detail : fallback;
    });
  }

  function commands(note, after) {
    var queue = Promise.resolve();
    return function (method, url, body) {
      var opts = { method: method };
      if (body) {
        opts.headers = { "Content-Type": "application/json" };
        opts.body = JSON.stringify(body);
      }
      queue = queue.then(function () {
        return fetch(url, opts).then(function (r) {
          if (!r.ok) return errorText(r, SPOTIFY_DOWN_TEXT).then(note);
        }, function () {
          note(SPOTIFY_DOWN_TEXT);
        });
      }).then(function () {
        after();
      });
      return queue;
    };
  }

  function freshVolume(note) {
    return getJSON("/api/spotify/now?fresh=1").then(function (n) {
      return n.volume;
    }, function () {
      if (note) note(SPOTIFY_DOWN_TEXT);
      return null;
    });
  }

  function volumeStep(volume, delta) {
    if (typeof volume !== "number") return null;
    return Math.max(0, Math.min(100, volume + delta));
  }

  function volumeChain(o) {
    var last = null;
    return function (delta) {
      var chained = last && Date.now() - last.at < CHAIN_MS;
      var sent = (chained ? last.sent : o.fresh()).then(function (volume) {
        var next = volumeStep(volume, delta);
        if (next === null) {
          o.skip();
          return null;
        }
        o.set(next);
        return o.put(next).then(function () {
          return next;
        });
      });
      last = { sent: sent, at: Date.now() };
      return sent;
    };
  }

  function formatMs(ms) {
    var s = Math.max(0, Math.floor((ms || 0) / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function progressAt(n, sinceMs) {
    if (!n || typeof n.progress_ms !== "number") return 0;
    var ms = n.progress_ms + (n.playing && !n.stale ? Math.max(0, sinceMs || 0) : 0);
    return n.duration_ms ? Math.min(ms, n.duration_ms) : ms;
  }

  function nextPollMs(n, pollMs, endMs) {
    if (!n || !n.playing || typeof n.progress_ms !== "number" || !n.duration_ms) return pollMs;
    var left = n.duration_ms - n.progress_ms + endMs;
    return Math.max(endMs, Math.min(pollMs, left));
  }

  function backoffMs(n, fails, pollMs, maxMs, endMs) {
    if (!fails) return nextPollMs(n, pollMs, endMs);
    return Math.min(maxMs, pollMs * Math.pow(2, fails - 1));
  }

  function staleText(n) {
    if (!n || !n.stale) return null;
    var why = n.reason === "rate limited" ? "Could not reach Spotify (too many requests)." : "Could not reach Spotify.";
    return n.track ? why + " Showing the last known song." : why;
  }

  function deviceText(n) {
    if (!n || !n.device) return null;
    return n.on_board ? "Playing on Home Board" : "Playing on " + n.device;
  }

  function nextBackoff(seconds) {
    return Math.min(seconds * 2, MAX_BACKOFF_S);
  }

  function events(on) {
    var backoff = 1;

    function retry() {
      setTimeout(connect, backoff * 1000);
      backoff = nextBackoff(backoff);
    }

    function connect() {
      if (typeof EventSource === "undefined") return;
      var es;
      try {
        es = new EventSource("/api/events");
      } catch (e) {
        retry();
        return;
      }
      es.onopen = function () { backoff = 1; };
      Object.keys(on).forEach(function (name) {
        [].concat(on[name]).forEach(function (fn) { es.addEventListener(name, fn); });
      });
      es.onerror = function () {
        es.close();
        retry();
      };
    }

    connect();
  }

  function holdStep(btn, step) {
    var delay = null;
    var repeat = null;
    function stop() {
      clearTimeout(delay);
      clearInterval(repeat);
      delay = repeat = null;
    }
    btn.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      stop();
      step();
      delay = setTimeout(function () {
        repeat = setInterval(step, HOLD_REPEAT_MS);
      }, HOLD_DELAY_MS);
    });
    btn.addEventListener("pointerup", stop);
    btn.addEventListener("pointerleave", stop);
    btn.addEventListener("pointercancel", stop);
    btn.addEventListener("click", function (e) {
      if (e.detail === 0) step();
    });
  }

  var Player = {
    SPOTIFY_DOWN_TEXT: SPOTIFY_DOWN_TEXT,
    getJSON: getJSON,
    send: send,
    errorText: errorText,
    commands: commands,
    freshVolume: freshVolume,
    volumeStep: volumeStep,
    volumeChain: volumeChain,
    formatMs: formatMs,
    progressAt: progressAt,
    nextPollMs: nextPollMs,
    backoffMs: backoffMs,
    staleText: staleText,
    deviceText: deviceText,
    nextBackoff: nextBackoff,
    events: events,
    holdStep: holdStep,
  };

  root.Player = Player;
  if (typeof module !== "undefined" && module.exports) module.exports = Player;
})(typeof window !== "undefined" ? window : globalThis);
