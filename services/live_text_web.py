"""Live Text in a web browser: what Voice to Text hears, as it hears it,
for any phone, tablet or screen on the network - a confidence monitor, a
captioner, a pastor's tablet - with nothing to install.

The page mirrors the desktop's Live Text window (ui/voice_live_window.py):
a scrolling history of finished lines with their speaker and highlights,
each channel's latest words under it, and the engine's state on top.

Two halves, so nothing from the web threads ever touches the app:

    LiveTextFeed      filled on the Tk thread (MainWindow.process_messages
                      calls pump()), and only while someone is watching -
                      names, highlights and the engine's state are read
                      there, exactly as the desktop window reads them.
    LiveTextWebServer a plain threaded HTTP server that serves the page
                      and hands out what the feed holds, under its lock.

Read-only and unauthenticated, like a TV in the foyer: it is off unless
Settings turns it on, and it listens on the same adapter as the phone
server, so a machine with a console-only network card can keep it there.
"""
import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from services import voice_highlights
from services.log_store import log

DEFAULT_PORT = 8780

# A browser that has not asked for anything this long has gone; the
# engine stops working out mid-sentence text for it.
VIEWER_TIMEOUT_SECONDS = 5.0

# The same 1000 lines the engine keeps (VoiceEngine.recent).
MAX_LINES = 1000

# Matches ui/voice_live_window.py.
UNSURE_BELOW = 0.45
OK_FG = "#66bb6a"
WAIT_FG = "#ffb74d"
ERROR_FG = "#ef5350"
OFF_FG = "#777777"


def default_config():
    return {"enabled": False, "port": DEFAULT_PORT}


def normalize(config):
    """The stored "live_web" settings, with anything missing or broken
    put back to its default."""
    result = default_config()

    if isinstance(config, dict):
        result["enabled"] = bool(config.get("enabled", False))
        try:
            port = int(config.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        if 1 <= port <= 65535:
            result["port"] = port

    return result


def _ink_for(color):
    """Text colour that reads on a highlight of `color`, as the desktop
    window picks it."""
    try:
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        color, rgb = voice_highlights.DEFAULT_COLOR, (0xF9, 0xA8, 0x25)
    luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return color, "#1a1a1a" if luma > 150 else "#ffffff"


class LiveTextFeed:
    """What the page shows, kept up to date from the Tk thread.

    get_speaker(number) -> (name, user) and mark(text, language) ->
    (text, highlights) are MainWindow's voice_speaker and voice_mark -
    the same ones the desktop window is given.
    """

    def __init__(self, engine, get_speaker, mark):
        self.engine = engine
        self.get_speaker = get_speaker
        self.mark = mark

        self._lock = threading.Lock()
        # A fresh one per run of the app, so an open page notices CLMix
        # was restarted and starts over rather than waiting for lines
        # newer than ones that no longer exist.
        self.epoch = secrets.token_hex(4)
        self._lines = deque(maxlen=MAX_LINES)
        self._live = []
        self._hint = ""
        self._status = {}
        self._last_time = 0.0
        # The engine's Clear cut-off as of the last pump - see
        # VoiceEngine.cleared_at. Sent to open pages so they clear too.
        self._cleared_at = 0.0
        # Set from the web threads, read on the Tk thread.
        self._viewer_seen_at = 0.0
        # Whether this feed holds one of engine.watchers - the engine
        # only keeps mid-sentence text while someone is watching.
        self._watching = False

    # --- Web threads ---

    def touch(self):
        self._viewer_seen_at = time.monotonic()

    def since(self, after):
        """Everything the page needs, with only the lines newer than
        `after` (a line's time, from the previous answer)."""
        with self._lock:
            after = max(after, self._cleared_at)
            lines = [line for line in self._lines if line["t"] > after]
            return {
                "epoch": self.epoch,
                "cleared_at": self._cleared_at,
                "lines": lines,
                "live": self._live,
                "hint": self._hint,
                "status": self._status,
            }

    # --- Tk thread ---

    def viewing(self):
        return time.monotonic() - self._viewer_seen_at < VIEWER_TIMEOUT_SECONDS

    def pump(self):
        """Bring the feed up to date - a no-op while nobody is watching,
        beyond giving back the engine's watcher."""
        watching = self.viewing()

        if watching != self._watching:
            self._watching = watching
            if watching:
                self.engine.watchers += 1
            else:
                self.engine.watchers = max(0, self.engine.watchers - 1)

        if not watching:
            return

        cleared_at = self.engine.cleared_at
        new_lines = self._new_lines()
        live, hint = self._live_rows()
        status = self._status_row()

        with self._lock:
            if cleared_at != self._cleared_at:
                self._cleared_at = cleared_at
                while self._lines and self._lines[0]["t"] <= cleared_at:
                    self._lines.popleft()
            self._lines.extend(new_lines)
            self._live = live
            self._hint = hint
            self._status = status

    def release(self):
        """Give the engine's watcher back - the server is stopping."""
        if self._watching:
            self._watching = False
            self.engine.watchers = max(0, self.engine.watchers - 1)
        self._viewer_seen_at = 0.0

    def _parts(self, text, language=None):
        """[[text, highlight colour or None, ink or None], ...]"""
        text, spans = self.mark(text, language)
        parts = []
        at = 0
        for span in spans:
            if span["start"] > at:
                parts.append([text[at:span["start"]], None, None])
            color, ink = _ink_for(span.get("color"))
            parts.append([text[span["start"]:span["end"]], color, ink])
            at = span["end"]
        if at < len(text):
            parts.append([text[at:], None, None])
        return parts

    def _new_lines(self):
        new = []
        for line in self.engine.visible_recent():
            if line["time"] <= self._last_time:
                continue
            self._last_time = line["time"]
            name, _user = self.get_speaker(line["channel"])
            confidence = line.get("confidence")
            new.append({
                "t": line["time"],
                "at": time.strftime("%H:%M:%S", time.localtime(line["time"])),
                "ch": line["channel"],
                "name": name,
                "lang": (line.get("language") or "").upper(),
                "unsure": confidence is not None and confidence < UNSURE_BELOW,
                "parts": self._parts(line["text"], line.get("language")),
            })
        return new

    def _live_rows(self):
        latest = {}
        for line in self.engine.visible_recent():
            latest[line["channel"]] = (line["text"], False,
                                       line.get("language"), line["time"])
        for number, text in self.engine.partials().items():
            latest[number] = (text, True, None, None)

        rows = []
        for number, (text, speaking, language, at) in sorted(latest.items()):
            name, _user = self.get_speaker(number)
            rows.append({
                "ch": number,
                "t": at,
                "name": name,
                "speaking": speaking,
                "parts": self._parts(text, None if speaking else language),
            })

        return rows, ("" if rows else self._gate_hint())

    def _gate_hint(self):
        engine = self.engine
        levels = engine.levels()
        gate = getattr(engine, "_gate_db", None)
        if engine.state != "running" or not levels or gate is None:
            return ""
        loudest = max(levels.values())
        if loudest >= gate:
            return ""
        return (f"Loudest input is about {round(loudest / 5) * 5} dBFS, "
                f"under the {gate} dBFS gate in Settings.")

    def _status_row(self):
        engine = self.engine
        state = engine.state
        word = {"running": "LISTENING", "starting": "STARTING",
                "error": "STOPPED"}.get(state, "OFF")
        where = getattr(engine, "where", "")
        if state == "running" and where:
            word += f" · {where}"
        return {
            "color": {"running": OK_FG, "starting": WAIT_FG,
                      "error": ERROR_FG}.get(state, OFF_FG),
            "word": word,
            "message": engine.message,
            "error": state == "error",
        }


class _Handler(BaseHTTPRequestHandler):
    # Set on the subclass LiveTextWebServer makes.
    feed = None

    server_version = "CLMix"

    def do_GET(self):
        url = urlsplit(self.path)

        if url.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif url.path == "/api/live":
            self.feed.touch()
            try:
                after = float(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                after = 0.0
            body = json.dumps(self.feed.since(after),
                              separators=(",", ":")).encode("utf-8")
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        # A page polls several times a second - not log material.
        pass


class _Server(ThreadingHTTPServer):
    # HTTPServer turns SO_REUSEADDR on, which on Windows lets a second
    # program bind a port already in use - both then "work" and neither
    # reliably gets the browsers. Off, a taken port fails at start() and
    # Settings can say so.
    allow_reuse_address = False
    daemon_threads = True


class LiveTextWebServer:
    """Serves the feed on `port`. start() raises OSError if the port
    will not open (in use, say); stop() is safe to call any time."""

    def __init__(self, feed, port, bind_ip=""):
        self.feed = feed
        self.port = port
        self.bind_ip = bind_ip or ""
        self._httpd = None
        self._thread = None

    def start(self):
        handler = type("LiveTextHandler", (_Handler,), {"feed": self.feed})
        self._httpd = _Server((self.bind_ip, self.port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.1},
            name="LiveTextWeb", daemon=True
        )
        self._thread.start()
        log("info", f"Live Text web page on "
            f"{self.bind_ip or 'all adapters'}:{self.port}")

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        # Waits (at most a poll_interval) for serve_forever to notice, so
        # the port is free again by the time a restart on it binds.
        httpd.shutdown()
        httpd.server_close()
        self.feed.release()
        log("info", "Live Text web page stopped")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1c1c1c">
<title>CLMix Live Text</title>
<style>
  :root {
    --bg: #1c1c1c; --log: #161616; --text: #ececec; --meta: #a9acb4;
    --live: #8fd3ff; --unsure: #9a9a9a; --error: #ef5350;
    --size: 20px;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
    display: flex; flex-direction: column;
    padding: 10px max(10px, env(safe-area-inset-right)) max(10px, env(safe-area-inset-bottom)) max(10px, env(safe-area-inset-left));
  }
  header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
  #dot { font-size: 18px; color: #777; line-height: 1; }
  #word { font-weight: 700; font-size: 13px; letter-spacing: .02em; color: #777; }
  #message { color: var(--meta); font-size: 13px; flex: 1; min-width: 0;
             overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #message.error { color: var(--error); }
  #offline { color: var(--error); font-size: 13px; font-weight: 600; display: none; }
  button {
    background: #2f2f2f; color: var(--text); border: 1px solid #3c3c3c;
    border-radius: 6px; padding: 6px 12px; font: inherit; font-size: 14px;
    cursor: pointer; min-height: 34px;
  }
  button:hover { background: #3a3a3a; }
  .box { background: var(--log); border-radius: 6px; padding: 10px 14px;
         font-size: var(--size); line-height: 1.35; overflow-wrap: anywhere; }
  #history { flex: 1; overflow-y: auto; min-height: 0; }
  #history p { margin: 0 0 .45em; }
  #live { margin-top: 8px; max-height: 40vh; overflow-y: auto; }
  #live p { margin: 0; }
  .meta { color: var(--meta); font-size: .6em; margin-right: .5em; white-space: nowrap; }
  .name { font-weight: 700; }
  .unsure { color: var(--unsure); }
  .speaking { color: var(--live); font-style: italic; }
  .idle { color: var(--meta); }
  .hl { font-weight: 700; border-radius: 3px; padding: 0 .12em; }
  #jump { position: fixed; right: 18px; bottom: 30%; display: none; }
</style>
</head>
<body>
<header>
  <span id="dot">●</span>
  <span id="word">CONNECTING</span>
  <span id="message"></span>
  <span id="offline">CLMix not reachable</span>
  <button id="smaller" title="Smaller text">A−</button>
  <button id="bigger" title="Bigger text">A+</button>
  <button id="save">Save…</button>
  <button id="clear">Clear</button>
</header>
<div id="history" class="box"></div>
<div id="live" class="box"><p class="idle">Nothing heard yet.</p></div>
<button id="jump">Latest ↓</button>
<script>
(function () {
  "use strict";
  var SIZES = [12, 14, 17, 20, 24, 30, 36, 44, 54, 64];
  var POLL_MS = 300;
  var history = document.getElementById("history");
  var live = document.getElementById("live");
  var jump = document.getElementById("jump");
  var since = 0, epoch = null, liveShown = null, failures = 0, lastData = null;
  // This browser's own Clear, kept across reloads: lines at or before it
  // are not shown again. Only here - a viewer cannot clear CLMix.
  var localCleared = 0;
  try { localCleared = parseFloat(localStorage.getItem("clmixLiveCleared")) || 0; } catch (e) {}
  var size = 3;

  try { var saved = parseInt(localStorage.getItem("clmixLiveSize"), 10);
        if (saved >= 0 && saved < SIZES.length) size = saved; } catch (e) {}

  function applySize() {
    document.documentElement.style.setProperty("--size", SIZES[size] + "px");
    try { localStorage.setItem("clmixLiveSize", String(size)); } catch (e) {}
  }
  applySize();
  document.getElementById("bigger").onclick = function () {
    size = Math.min(SIZES.length - 1, size + 1); applySize(); };
  document.getElementById("smaller").onclick = function () {
    size = Math.max(0, size - 1); applySize(); };
  document.getElementById("clear").onclick = function () {
    history.textContent = "";
    // The newest line's own time (CLMix's clock), not this device's -
    // a phone running ahead would otherwise hide lines still to come.
    localCleared = Math.max(localCleared, since);
    try { localStorage.setItem("clmixLiveCleared", String(localCleared)); } catch (e) {}
    liveShown = null;
    if (lastData) render(lastData);
  };
  document.getElementById("save").onclick = function () {
    var text = history.innerText.trim();
    if (!text) { alert("Nothing to save yet."); return; }
    var a = document.createElement("a");
    var d = new Date(), pad = function (n) { return (n < 10 ? "0" : "") + n; };
    a.download = "transcript-" + d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" +
      pad(d.getDate()) + "-" + pad(d.getHours()) + pad(d.getMinutes()) + ".txt";
    a.href = URL.createObjectURL(new Blob([text + "\n"], {type: "text/plain"}));
    document.body.appendChild(a); a.click(); a.remove();
  };

  function atBottom() {
    return history.scrollHeight - history.scrollTop - history.clientHeight < 8;
  }
  history.addEventListener("scroll", function () {
    jump.style.display = atBottom() ? "none" : "block"; });
  jump.onclick = function () { history.scrollTop = history.scrollHeight; };

  function addParts(el, parts) {
    parts.forEach(function (p) {
      if (p[1]) {
        var s = document.createElement("span");
        s.className = "hl"; s.style.background = p[1]; s.style.color = p[2];
        s.textContent = p[0]; el.appendChild(s);
      } else {
        el.appendChild(document.createTextNode(p[0]));
      }
    });
  }
  function span(cls, text) {
    var s = document.createElement("span"); s.className = cls; s.textContent = text; return s;
  }

  function hidden(t) {
    return t !== null && t !== undefined && t <= localCleared;
  }

  function render(data) {
    lastData = data;
    // Cleared on CLMix itself: drop what is already on screen too.
    if (data.cleared_at) {
      var cut = data.cleared_at;
      Array.prototype.slice.call(history.children).forEach(function (p) {
        if (parseFloat(p.dataset.t) <= cut) history.removeChild(p);
      });
    }
    if (data.epoch !== epoch) {
      // CLMix restarted: start over.
      epoch = data.epoch; history.textContent = ""; liveShown = null;
      if (since) { since = 0; return false; }
    }
    if (data.lines.length) {
      var follow = atBottom();
      data.lines.forEach(function (line) {
        since = line.t;
        if (hidden(line.t)) return;
        var p = document.createElement("p");
        p.dataset.t = String(line.t);
        p.appendChild(span("meta", line.at + "  Ch " + line.ch + (line.lang ? "  " + line.lang : "")));
        p.appendChild(span("name", line.name + ": "));
        var body = span(line.unsure ? "unsure" : "", "");
        addParts(body, line.parts); p.appendChild(body);
        history.appendChild(p);
      });
      while (history.childElementCount > 1000) history.removeChild(history.firstChild);
      if (follow) history.scrollTop = history.scrollHeight;
    }
    var rows = data.live.filter(function (row) { return !hidden(row.t); });
    var key = JSON.stringify([rows, data.hint]);
    if (key !== liveShown) {
      liveShown = key; live.textContent = "";
      if (!rows.length) {
        var idle = document.createElement("p"); idle.className = "idle";
        idle.textContent = "Nothing heard yet." + (data.hint ? " " + data.hint : "");
        live.appendChild(idle);
      }
      rows.forEach(function (row) {
        var p = document.createElement("p");
        p.appendChild(span("name", row.name + ": "));
        var body = span(row.speaking ? "speaking" : "", "");
        addParts(body, row.parts);
        if (row.speaking) body.appendChild(document.createTextNode(" …"));
        p.appendChild(body); live.appendChild(p);
      });
    }
    var st = data.status || {};
    document.getElementById("dot").style.color = st.color || "#777";
    var word = document.getElementById("word");
    word.textContent = st.word || "OFF"; word.style.color = st.color || "#777";
    var msg = document.getElementById("message");
    msg.textContent = st.message || ""; msg.className = st.error ? "error" : "";
    return true;
  }

  function poll() {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "api/live?since=" + since);
    xhr.timeout = 4000;
    xhr.onload = function () {
      var again = POLL_MS;
      if (xhr.status === 200) {
        failures = 0;
        document.getElementById("offline").style.display = "none";
        try { if (render(JSON.parse(xhr.responseText)) === false) again = 0; } catch (e) { console.error("render failed: " + e + " " + e.stack); }
      } else { fail(); }
      setTimeout(poll, again);
    };
    xhr.onerror = xhr.ontimeout = function () { fail(); setTimeout(poll, 2000); };
    xhr.send();
  }
  function fail() {
    if (++failures >= 2) document.getElementById("offline").style.display = "inline";
  }
  poll();
})();
</script>
</body>
</html>
"""
