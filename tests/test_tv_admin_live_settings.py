import json
import subprocess

from tests.test_tv_admin import SETTINGS_JS, TV_HARNESS, needs_node

EVENTS = r"""
const sources = [];
globalThis.EventSource = function (url) {
  this.url = url;
  this.listeners = {};
  this.closed = false;
  sources.push(this);
};
EventSource.prototype.addEventListener = function (k, fn) { this.listeners[k] = fn; };
EventSource.prototype.close = function () { this.closed = true; };
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
"""


def _tv(body):
    script = (
        f"{TV_HARNESS}\n{EVENTS}\nrequire({json.dumps(str(SETTINGS_JS))});\n"
        f"(async () => {{\n{body}\n}})();"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_settings_changed_event_reloads_slideshow_weather_clock_and_caption():
    r = _tv(
        """
        await settle();
        const es = sources[0];
        server.settings = Object.assign({}, server.settings, {
          weather_location: "30263", weather_name: "Newnan", clock_size: 7, caption_size: 3, interval_s: 45,
        });
        es.listeners.hello();
        es.listeners.settings_changed();
        await settle();
        const after = [all["weather-location"].value, all["weather-name"].textContent,
          all["clock-size"].value, all["caption-size"].value, all["interval"].value];
        console.log(JSON.stringify({ url: es.url, count: sources.length, after }));
        """
    )
    assert r["url"] == "/api/events" and r["count"] == 1
    assert r["after"] == ["30263", "Showing weather for Newnan", 7, 3, 45]


@needs_node
def test_events_reconnect_with_backoff_and_reload_after_reconnect():
    r = _tv(
        """
        await settle();
        sources[0].listeners.hello();
        sources[0].onerror();
        const closed = sources[0].closed;
        const delay = timers[timers.length - 1].ms;
        timers[timers.length - 1].fn();
        server.settings = Object.assign({}, server.settings, { clock_size: 9 });
        sources[1].listeners.hello();
        await settle();
        console.log(JSON.stringify({ closed, delay, count: sources.length, clock: all["clock-size"].value }));
        """
    )
    assert r == {"closed": True, "delay": 1000, "count": 2, "clock": 9}
