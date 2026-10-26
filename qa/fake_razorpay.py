"""A stand-in Razorpay (invoice endpoints) on :8766 for the browser check, keys rzp_test_abc123 / ks_test. Invented data only.
Run: APP_ENV=development APP_URL=http://localhost:8000 python qa/fake_razorpay.py  (hub with RAZORPAY_API_BASE=http://127.0.0.1:8766/v1)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import httpx
import uvicorn
from tests.test_razorpay_invoices import FakeRazorpay

fake = FakeRazorpay()


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body"):
            break
    url = f"http://x{scope['path']}" + (f"?{scope['query_string'].decode()}" if scope["query_string"] else "")
    headers = {k.decode(): v.decode() for k, v in scope["headers"]}
    try:
        r = fake.handler(httpx.Request(scope["method"], url, headers=headers, content=body))
    except AssertionError as e:
        r = httpx.Response(401, json={"error": {"code": "BAD_REQUEST_ERROR", "description": f"refused: {e}"}})
    await send({"type": "http.response.start", "status": r.status_code, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": r.content})

uvicorn.run(app, port=8766, log_level="warning")
