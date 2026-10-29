"""
GeM relay (AWS Lambda, Asia Pacific (Mumbai) ap-south-1): GeM's bid search answers only from India and the hub runs in
Railway's US West, so the hub sends its GeM requests here and this function makes them from Mumbai.

- Only bidplus.gem.gov.in, and only the paths the tender finder uses (the bid search and the bid documents): not an open proxy.
- Every request needs the X-Relay-Key header to equal the RELAY_KEY environment variable (the hub's GEM_RELAY_KEY).
- Cookies go both ways untouched: GeM's search token belongs to the session cookie it sets with it.
- Redirects are passed back, not followed, the same as the hub's own client.
- No packages to install: the Python standard library only. Paste this file as lambda_function.py, handler
  lambda_function.lambda_handler, timeout 30 seconds.
"""
import base64
import hmac
import os
import urllib.error
import urllib.request

GEM = "https://bidplus.gem.gov.in"
KEY = os.environ.get("RELAY_KEY", "")
EXACT = ("/all-bids", "/all-bids-data")
PREFIXES = ("/showbidDocument/", "/showradocumentPdf/", "/showdirectradocumentPdf/", "/list-ra-schedules/")
FORWARD = ("accept", "content-type", "referer", "user-agent", "x-requested-with")
TIMEOUT = 25
MAX_BYTES = 4_400_000        # a Function URL answers at most 6 MB, and the body goes back in base64 (4/3 bigger)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def _plain(status: int, text: str) -> dict:
    return {"statusCode": status, "headers": {"content-type": "text/plain"}, "body": text}


def lambda_handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not KEY or not hmac.compare_digest(headers.get("x-relay-key", ""), KEY):
        return _plain(403, "forbidden")
    path = event.get("rawPath") or "/"
    if path not in EXACT and not path.startswith(PREFIXES):
        return _plain(404, "not a GeM bid path")
    method = event["requestContext"]["http"]["method"]
    if method not in ("GET", "POST"):
        return _plain(405, "GET or POST only")

    body = None
    if method == "POST" and event.get("body") is not None:
        body = base64.b64decode(event["body"]) if event.get("isBase64Encoded") else event["body"].encode()
    query = event.get("rawQueryString")
    req = urllib.request.Request(GEM + path + (f"?{query}" if query else ""), data=body, method=method,
                                 headers={k: headers[k] for k in FORWARD if k in headers})
    if event.get("cookies"):
        req.add_header("Cookie", "; ".join(event["cookies"]))

    try:
        r = OPENER.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:      # 3xx (not followed), 4xx and 5xx: hand GeM's answer back as it is
        r = e
    except (urllib.error.URLError, TimeoutError) as e:
        return _plain(504, f"GeM didn't answer the relay: {e}")
    with r:
        data = r.read(MAX_BYTES + 1)
        status = r.status if hasattr(r, "status") else r.code
        out = {k: r.headers[k] for k in ("content-type", "location") if r.headers.get(k)}
        cookies = r.headers.get_all("Set-Cookie") or []
    if len(data) > MAX_BYTES:
        return _plain(413, "GeM's answer is too big for the relay")
    return {"statusCode": status, "headers": out, "cookies": cookies,
            "body": base64.b64encode(data).decode(), "isBase64Encoded": True}
