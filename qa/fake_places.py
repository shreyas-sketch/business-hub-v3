"""A fake Google Places (New) server and fake business websites, for screenshots and manual checks of v2.4's
"Find new customers" on a local hub. Every business, address, phone number and website here is invented.

    python3 qa/fake_places.py 8098        # then run the hub with
    GOOGLE_PLACES_API_KEY=qa-fake GOOGLE_PLACES_API_BASE=http://127.0.0.1:8098/v1 FETCH_ALLOW_PRIVATE=true APP_ENV=development ...

Answers POST /v1/places:searchText and GET /v1/places/<id> with the fields the request's field mask asks for, and serves
each business's "own website" at /site/<id>.
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8098
BASE = f"http://127.0.0.1:{PORT}"

# (id, name, kind words, primary type label, area, status, phone, has website)
PLACES = [
    ("QAPLACE0001xx", "Invented Spice Distributors", "distributor fmcg food spice", "Wholesaler", "Unit 4, Invented Estate, Bhiwandi", "OPERATIONAL", "098111 00101", True),
    ("QAPLACE0002xx", "Sample Snacks Wholesale", "distributor fmcg food snacks", "Wholesaler", "Gala 12, Example Complex, Bhiwandi", "OPERATIONAL", "098111 00102", True),
    ("QAPLACE0003xx", "Testline Beverages Agency", "distributor fmcg beverage", "Distribution service", "Plot 7, Demo MIDC, Thane", "OPERATIONAL", "098111 00103", False),
    ("QAPLACE0004xx", "Placeholder Pickles House", "distributor fmcg food pickle", "Food manufacturer", "Shop 3, Mock Market, Kalyan", "CLOSED_PERMANENTLY", "098111 00104", False),
    ("QAPLACE0005xx", "Demo Garment Exports", "exporter garment textile", "Clothing manufacturer", "Building B, Fictional Park, Bhiwandi", "OPERATIONAL", "098111 00105", True),
    ("QAPLACE0006xx", "Example Home Textiles", "exporter garment textile home", "Textile mill", "Survey 21, Invented Road, Bhiwandi", "OPERATIONAL", "098111 00106", True),
    ("QAPLACE0007xx", "Mockup Online Sellers Hub", "ecommerce seller online", "Store", "Office 9, Sample Tower, Thane", "OPERATIONAL", "098111 00107", True),
    ("QAPLACE0008xx", "Trial Cosmetics Traders", "ecommerce seller cosmetics distributor", "Cosmetics store", "Shop 18, Test Bazaar, Thane", "OPERATIONAL", "098111 00108", False),
]
SITES = {
    "QAPLACE0001xx": ("We distribute spices and ready mixes to 300 kirana stores across Bhiwandi and Thane.",
                      "QA-UNIQUE-PHRASE-SAFFRON-TRUCKS"),
    "QAPLACE0002xx": ("Namkeen and snacks wholesale for retailers, with daily van delivery.", "QA-UNIQUE-PHRASE-DAILY-VANS"),
    "QAPLACE0005xx": ("Knitted garments for export buyers in Europe. 40 machines, in-house finishing.", "QA-UNIQUE-PHRASE-FORTY-MACHINES"),
    "QAPLACE0006xx": ("Bedsheets and towels for export and large retail chains.", "QA-UNIQUE-PHRASE-TOWEL-LOOMS"),
    "QAPLACE0007xx": ("We help small brands sell on online marketplaces: listing, packing and dispatch.", "QA-UNIQUE-PHRASE-DISPATCH-DESK"),
}
MINE = ("QAPLACE9999xx", "Invented Packaging Works", "packaging", "Packaging supply store", "Gala 1, Invented Estate, Bhiwandi",
        "OPERATIONAL", "098200 00007", False)   # the owner's own business: a search should leave it out


def _place(p, fields: set[str], prefix: str = "") -> dict:
    pid, name, _kind, label, address, status, phone, site = p
    full = {
        "id": pid, "displayName": {"text": name, "languageCode": "en"}, "formattedAddress": address, "types": ["establishment"],
        "primaryTypeDisplayName": {"text": label, "languageCode": "en"}, "businessStatus": status,
        "googleMapsUri": f"https://maps.google.com/?cid=qa-{pid}", "location": {"latitude": 19.2965, "longitude": 73.0631},
        "nationalPhoneNumber": phone, "internationalPhoneNumber": "+91 " + phone.lstrip("0"),
        "rating": 4.2, "userRatingCount": 37,
    }
    if site:
        full["websiteUri"] = f"{BASE}/site/{pid}"
    if not fields or "*" in fields:
        return full
    out = {}
    for f in fields:
        key = f[len(prefix):] if prefix and f.startswith(prefix) else f
        if key in full:
            out[key] = full[key]
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def _fields(self) -> set[str]:
        return {f.strip() for f in (self.headers.get("X-Goog-FieldMask") or "").split(",") if f.strip()}

    def do_POST(self):
        if not self.path.startswith("/v1/places:searchText"):
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        words = set(re.findall(r"[a-z]+", (body.get("textQuery") or "").lower()))
        hits = [p for p in PLACES + [MINE] if words & set(p[2].split())] or PLACES[:3]
        size = max(1, min(20, int(body.get("pageSize") or 20)))
        return self._send(200, {"places": [_place(p, self._fields(), "places.") for p in hits[:size]]})

    def do_GET(self):
        m = re.match(r"^/v1/places/([A-Za-z0-9_-]+)", self.path)
        if m:
            p = next((x for x in PLACES + [MINE] if x[0] == m.group(1)), None)
            return self._send(200, _place(p, self._fields())) if p else self._send(404, {"error": {"status": "NOT_FOUND"}})
        m = re.match(r"^/site/([A-Za-z0-9_-]+)", self.path)
        if m and m.group(1) in SITES:
            p = next(x for x in PLACES if x[0] == m.group(1))
            about, phrase = SITES[m.group(1)]
            html = (f"<!doctype html><html><head><title>{p[1]}</title><meta name='description' content='{about}'></head>"
                    f"<body><h1>{p[1]}</h1><p>{about}</p><p>{phrase}</p><p>Call us: {p[6]}</p></body></html>")
            return self._send(200, html, "text/html; charset=utf-8")
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"fake Places on {BASE}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
