"""Stand-in for 200 attendees' own websites (all made up): /b/<n>/ with an about page, a contact page and a logo.
Realistic small-business behaviour: most open in 0.3–4 s, some are slow (8–15 s), a few never answer in time (25 s, past the
hub's 20 s limit) and a few are broken (404). Seeded, so every run sees the same mix."""
import io
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw

TRADES = [("Appliance repair", "Washing machine, fridge and AC repair at home"), ("Interiors", "Modular kitchens and wardrobes"),
          ("Dental clinic", "Family dentistry, braces and implants"), ("Sweets and farsan", "Fresh sweets, farsan and festival boxes"),
          ("CA firm", "GST filing, audits and company registration"), ("Packaging", "Corrugated boxes and printed cartons"),
          ("Travel agency", "Holiday packages and visa help"), ("Coaching classes", "Maths and science coaching for classes 8 to 12"),
          ("Solar installer", "Rooftop solar for homes and factories"), ("Electrical contractor", "Wiring, panels and maintenance for offices")]
CITIES = ["Pune", "Mumbai", "Thane", "Surat", "Nagpur", "Nashik", "Ahmedabad", "Indore", "Jaipur", "Bengaluru"]
COLOURS = [(196, 64, 32), (28, 84, 140), (15, 118, 110), (124, 45, 18), (109, 40, 217), (180, 83, 9)]
rng = random.Random(7)
PROFILE = {}
for n in range(1, 1001):
    r = rng.random()
    kind = "broken" if r < 0.04 else "dead" if r < 0.08 else "slow" if r < 0.20 else "ok"
    PROFILE[n] = (kind, rng.uniform(0.3, 4.0) if kind == "ok" else rng.uniform(8, 15) if kind == "slow" else 25)


def logo(n: int) -> bytes:
    img = Image.new("RGB", (240, 120), "#FFFFFF")
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 110, 110), fill=COLOURS[n % len(COLOURS)])
    d.rectangle((120, 30, 230, 90), fill=COLOURS[(n + 2) % len(COLOURS)])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


LOGOS = {i: logo(i) for i in range(len(COLOURS))}


def page(n: int, which: str) -> bytes:
    trade, what = TRADES[n % len(TRADES)]
    city = CITIES[n % len(CITIES)]
    name = f"Sample {trade} {n}"
    if which == "contact":
        return f"<html><head><title>Contact</title></head><body><p>{city}. Call 98{n:08d}.</p><p>hello{n}@sample-business.example</p></body></html>".encode()
    if which == "about":
        return f"<html><head><title>About</title></head><body><h1>About us</h1><p>{name} has served {city} for {5 + n % 20} years. {what}.</p></body></html>".encode()
    heads = "".join(f"<h2>{x}</h2><p>Details about {x.lower()}.</p>" for x in what.replace(" and ", ", ").split(", ")[:4])
    return f"""<html><head><title>{name} | {trade} in {city}</title><meta name="description" content="{what} in {city}.">
<meta name="viewport" content="width=device-width"></head><body><img src="logo.png" alt="{name} logo"><h1>{name}</h1>
<p>{what} in {city}. Trusted by local families and businesses.</p>{heads}<h2>Why choose us</h2><p>On-time work. Clear quotes.</p>
<a href="about">About us</a> <a href="contact">Contact us</a> <a href="https://wa.me/9198{n:08d}">WhatsApp</a>
<p>{('Lorem ipsum ' * 200)}</p></body></html>""".encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):  # noqa: N802
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if len(parts) < 2 or parts[0] != "b" or not parts[1].isdigit():
            return self._send(404, b"no", "text/html")
        n = int(parts[1])
        kind, delay = PROFILE.get(n, ("ok", 1.0))
        which = parts[2] if len(parts) > 2 else "home"
        if kind == "broken" and which == "home":
            return self._send(404, b"Not found", "text/html")
        time.sleep(delay if which == "home" else min(delay, 2.0) / 2)
        if which == "logo.png":
            return self._send(200, LOGOS[n % len(LOGOS)], "image/png")
        return self._send(200, page(n, which if which in ("about", "contact") else "home"), "text/html; charset=utf-8")

    def _send(self, code, body, ctype):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 512


Server(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8850), H).serve_forever()
