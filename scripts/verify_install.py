#!/usr/bin/env python3
"""verify_install.py — prove a CorvinOS install works, over the real wire.

Used as the last step of install.sh / update.sh / update.ps1 and by operators:

    python3 scripts/verify_install.py                 # console + auth + API
    python3 scripts/verify_install.py --voice         # + TTS → STT round trip
    python3 scripts/verify_install.py --voice --lang en --json

Every check goes through the same HTTP surface a browser uses — the SPA shell,
the hashed entry bundle, local login, the CSRF-protected API. "The unit is
active" and "healthz is green" are both known to be true while the UI is
broken (a console that booted before dist/ existed serves a 503 shell with a
green healthz), so neither counts as success on its own.

--voice synthesises a sentence with /voice/tts and feeds the audio back into
/voice/transcribe. Passing means text→speech→text survived with the words
intact, whichever tiers actually served (reported).

stdlib only: it must run with ANY python3, including when the CorvinOS venv
it is checking is broken. Exit 0 = all checks passed, 1 = a check failed.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PHRASES = {
    "de": "Guten Morgen, die Installation funktioniert einwandfrei.",
    "en": "Good morning, the installation works perfectly.",
}


class Check:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append({"check": name, "ok": ok, "detail": detail})
        return ok

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.results)


def _client():
    jar = http.cookiejar.CookieJar()
    # No proxy for loopback: a machine-wide proxy (common on corporate / Citrix
    # desktops) otherwise intercepts 127.0.0.1 and answers with its own page.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
    return opener, jar


def _req(opener, method: str, url: str, *, data: bytes | None = None,
         headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() if e.fp else b""


def _words(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z]+", s) if len(w) > 2}


def _multipart(field: str, filename: str, content: bytes, ctype: str,
               extra: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in extra.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def verify(base: str, *, voice: bool, lang: str, dist: Path | None, wait_s: float) -> Check:
    c = Check()
    opener, _jar = _client()

    # 1. SPA shell — wait for it, the service may still be booting.
    deadline = time.monotonic() + wait_s
    code, hdrs, body = 0, {}, b""
    while True:
        try:
            code, hdrs, body = _req(opener, "GET", f"{base}/console/", timeout=5)
        except OSError as e:
            code, body = 0, str(e).encode()
        if code == 200 or time.monotonic() > deadline:
            break
        time.sleep(1)
    html = body.decode("utf-8", "replace")
    entry = re.search(r'src="(/console/assets/index-[^"]+\.js)"', html)
    if not c.add("console shell (GET /console/ → 200 HTML)", code == 200 and entry is not None,
                 f"HTTP {code}" + ("" if entry else " — no entry bundle in the HTML (503 fallback page?)")):
        return c
    cc = hdrs.get("Cache-Control") or hdrs.get("cache-control") or ""
    c.add("shell is not cached (Cache-Control: no-cache)", "no-cache" in cc, cc or "missing")

    code, hdrs, js = _req(opener, "GET", base + entry.group(1))
    c.add("entry bundle loads", code == 200 and len(js) > 1000, f"{entry.group(1)} HTTP {code}, {len(js)} bytes")

    if dist is not None:
        idx = dist / "index.html"
        on_disk = re.search(r'src="(/console/assets/index-[^"]+\.js)"', idx.read_text("utf-8")) if idx.is_file() else None
        c.add("served bundle == freshly built bundle",
              bool(on_disk) and on_disk.group(1) == entry.group(1),
              f"served {entry.group(1)} vs built {on_disk.group(1) if on_disk else '(no dist/index.html)'}")

    code, _h, body = _req(opener, "GET", f"{base}/v1/console/healthz")
    c.add("healthz", code == 200, f"HTTP {code}")

    # 2. Local login → session cookie → CSRF token.
    code, _h, _b = _req(opener, "GET", f"{base}/v1/console/auth/local-login")
    code, _h, body = _req(opener, "GET", f"{base}/v1/console/auth/whoami")
    csrf = ""
    try:
        csrf = json.loads(body).get("csrf_token", "")
    except ValueError:
        pass
    if not c.add("local login issues a session + CSRF token", code == 200 and bool(csrf), f"whoami HTTP {code}"):
        return c
    auth = {"X-CSRF-Token": csrf}

    code, _h, body = _req(opener, "GET", f"{base}/v1/console/voice/status")
    c.add("voice status API", code == 200, f"HTTP {code}")

    if not voice:
        return c

    # 3. TTS → STT round trip.
    phrase = PHRASES.get(lang, PHRASES["en"])
    t0 = time.monotonic()
    code, hdrs, audio = _req(opener, "POST", f"{base}/v1/console/voice/tts",
                             data=json.dumps({"text": phrase, "lang": lang, "system_generated": True}).encode(),
                             headers={**auth, "Content-Type": "application/json"}, timeout=180)
    ctype = (hdrs.get("Content-Type") or hdrs.get("content-type") or "").split(";")[0]
    provider = hdrs.get("X-Corvin-TTS-Provider") or hdrs.get("x-corvin-tts-provider") or "?"
    reason = hdrs.get("X-Corvin-Voice-Reason") or hdrs.get("x-corvin-voice-reason") or ""
    if not c.add("text-to-speech produces audio", code == 200 and ctype.startswith("audio/") and len(audio) > 2000,
                 f"HTTP {code}, {ctype or 'no type'}, {len(audio)} bytes, provider={provider}"
                 + (f", reason={reason}" if reason else "") + f", {time.monotonic() - t0:.1f}s"):
        return c

    ext = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg"}.get(ctype, "bin")
    body, mp_ctype = _multipart("audio", f"probe.{ext}", audio, ctype, {"lang": lang})
    t0 = time.monotonic()
    code, _h, out = _req(opener, "POST", f"{base}/v1/console/voice/transcribe", data=body,
                         headers={**auth, "Content-Type": mp_ctype}, timeout=300)
    text = ""
    try:
        text = json.loads(out).get("text", "")
    except ValueError:
        pass
    want, got = _words(phrase), _words(text)
    overlap = len(want & got) / max(1, len(want))
    c.add("speech-to-text recognises the spoken sentence (≥60% of words)",
          code == 200 and overlap >= 0.6,
          f"HTTP {code}, {overlap:.0%} word match, {time.monotonic() - t0:.1f}s, heard: {text[:120]!r}")
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--voice", action="store_true", help="also run the TTS → STT round trip")
    ap.add_argument("--lang", default="de", choices=sorted(PHRASES))
    ap.add_argument("--dist", type=Path, help="web-next/dist of the build that must be served")
    ap.add_argument("--wait", type=float, default=120, help="seconds to wait for the console to come up")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    c = verify(a.url.rstrip("/"), voice=a.voice, lang=a.lang, dist=a.dist, wait_s=a.wait)
    if a.json:
        print(json.dumps({"ok": c.ok, "checks": c.results}, indent=2, ensure_ascii=False))
    else:
        for r in c.results:
            print(f"  {'✓' if r['ok'] else '✗'} {r['check']}" + (f"  — {r['detail']}" if r["detail"] else ""))
        print("  PASS" if c.ok else "  FAIL")
    return 0 if c.ok else 1


if __name__ == "__main__":
    sys.exit(main())
