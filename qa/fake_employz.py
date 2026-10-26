"""A stand-in Employz.ai (LeadConnector v2) on :8765 for the browser check: the test suite's invented sub-account, GET only."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "backend"))
import httpx
import uvicorn
from tests.test_employz_import import FakeEmployz

fake = FakeEmployz()


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    url = f"http://x{scope['path']}" + (f"?{scope['query_string'].decode()}" if scope["query_string"] else "")
    headers = {k.decode(): v.decode() for k, v in scope["headers"]}
    try:
        r = fake.handler(httpx.Request(scope["method"], url, headers=headers))
    except AssertionError as e:
        r = httpx.Response(418, json={"message": str(e)})
        print("REFUSED", e, flush=True)
    await send({"type": "http.response.start", "status": r.status_code, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": r.content})

uvicorn.run(app, port=8765, log_level="warning")
