"""The safe website reader (app/fetch.py) in v2.2: a packed (gzip) answer is unpacked in small steps and stopped at the size
limit, unknown packing is refused, a slow answer is cut off by the overall deadline, robots.txt goes through the same
checks (fetch_text), and shared/documentation address ranges count as not public. Everything served here is invented."""
import gzip
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import fetch
from app.config import settings

BOMB = gzip.compress(b"<html><body>" + b"a" * (40 * 1024 * 1024) + b"</body></html>")    # ~40 KB packed, 40 MB unpacked


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/bomb":
            body, extra = BOMB, {"Content-Encoding": "gzip"}
        elif self.path == "/small-gzip":
            body, extra = gzip.compress(b"<html><title>Test Works</title><body>Hello</body></html>"), {"Content-Encoding": "gzip"}
        elif self.path == "/brotli":
            body, extra = b"\x0b\x02\x80hello\x03", {"Content-Encoding": "br"}
        elif self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                for _ in range(60):
                    self.wfile.write(b"<p>x</p>")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass
            return
        elif self.path == "/robots.txt":
            body, extra = "﻿User-agent: *\nDisallow: /private\n".encode(), {}
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
            return
        elif self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/robots.txt")
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.server.seen.append(self.headers.get("Accept-Encoding"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in extra.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@contextmanager
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    finally:
        srv.shutdown()


async def test_a_packed_answer_is_unpacked_only_up_to_the_limit_and_we_ask_for_no_packing():
    with server() as (base, srv):
        page = await fetch.fetch(base + "/small-gzip")
        assert "Test Works" in page.html
        page = await fetch.fetch(base + "/bomb")
        assert page.bytes == fetch.MAX_BYTES and len(page.html) == fetch.MAX_BYTES
        assert set(srv.seen) == {"identity"}
        with pytest.raises(fetch.FetchError):
            await fetch.fetch(base + "/brotli")


async def test_a_slow_answer_is_cut_off_by_the_overall_deadline(monkeypatch):
    monkeypatch.setattr(fetch, "DEADLINE", 1)
    with server() as (base, _):
        started = time.monotonic()
        with pytest.raises(fetch.FetchError):
            await fetch.fetch(base + "/slow")
        assert time.monotonic() - started < 3


async def test_robots_txt_is_read_through_the_same_checks():
    with server() as (base, _):
        r = await fetch.fetch_text(base + "/moved")
        assert r.status == 200 and r.text.startswith("User-agent")        # the BOM is dropped, the redirect followed
        r = await fetch.fetch_text(base + "/nothing-here")
        assert r.status == 404 and r.text == ""


@pytest.mark.parametrize("ip", ["100.64.0.10", "192.0.2.10", "198.18.0.1", "10.0.0.1", "127.0.0.1", "169.254.169.254", "::1"])
def test_shared_and_documentation_ranges_are_not_public(ip):
    assert fetch._blocked(ip)


def test_public_addresses_are_allowed():
    assert not fetch._blocked("8.8.8.8") and not fetch._blocked("2606:4700:4700::1111")


async def test_without_the_test_switch_a_shared_address_is_refused(monkeypatch):
    monkeypatch.setattr(type(settings), "fetch_private_ok", property(lambda s: False))
    with pytest.raises(fetch.FetchError):
        await fetch.fetch_text("http://100.64.0.10/robots.txt")
