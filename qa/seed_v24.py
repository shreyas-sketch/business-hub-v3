"""Seed for the v2.4 checks (qa/screens_v24.py): one invented member on the v2.3 + v2.4 setup, on Membership, with six open
opportunities and one WhatsApp inquiry. Everything here is invented. It EMPTIES the database it points at, so it refuses to
run unless DB_NAME starts with "qa" and the hub is in development.

    cd backend && APP_ENV=development APP_URL=http://localhost:8000 DB_NAME=qa24 AI_PROVIDER=mock RUN_SCHEDULER=false \\
        ADMIN_PHONES=+919999900000 python3 ../qa/seed_v24.py

(seed_demo.py stops at the team step on v2.3 and v2.4, because the v2.3 setup hides the team pages it calls. That is older
than v2.4 and was left as it is.)
"""
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ADMIN_PHONES", "+919999900000")
if not os.environ.get("DB_NAME", "").startswith("qa") or os.environ["APP_ENV"] != "development":
    sys.exit("Refusing: set APP_ENV=development and a throwaway DB_NAME that starts with 'qa'.")

import httpx  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402

from app import v23, v24  # noqa: E402
from app.db import connect, now  # noqa: E402
from app.main import app  # noqa: E402

PHONE = "9820000007"
PROFILE = {"name": "Kaveri Home Appliances", "city": "Pune",
           "industry": "Retail and wholesale of kitchen and home appliances, with installation",
           "offers": [{"name": "Chimney with installation", "price": "₹14,500", "unit": "onwards"}, {"name": "Bulk orders for builders", "price": "", "unit": ""}],
           "ideal_customer": "Families setting up a new kitchen, and builders handing over flats", "why_us": "Same-day installation\nGenuine brands with bills",
           "proof": "", "whatsapp": "98200 00007", "email": "hello@kaveri-appliances.example", "language": "English"}
DEALS = [("Kunal Mehta", "98201 10001", 42000), ("Ritu Bansal", "98201 10002", None), ("Anil Desai", "98201 10003", 95000),
         ("Sneha Kulkarni", "98201 10004", None), ("Farhan Shaikh", "98201 10005", 24000), ("Pooja Iyer", "98201 10006", None)]


async def main():
    d = connect()
    for name in await d.list_collection_names():
        await d[name].delete_many({})
    async with LifespanManager(app):
        await v23.apply("seed")
        await v24.apply("seed")
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=os.environ.get("APP_URL", "http://localhost:8000"))
        code = (await c.post("/api/auth/otp", json={"phone": PHONE})).json()["dev_code"]
        await c.post("/api/auth/verify", json={"phone": PHONE, "code": code, "name": "Owner Test"})
        assert (await c.put("/api/business", json=PROFILE)).status_code == 200
        uid = (await c.get("/api/me")).json()["user"]["id"]
        await d.users.update_one({"_id": uid}, {"$set": {"access.lite": now() + timedelta(days=30)}})
        for n, p, v in DEALS:
            r = await c.post("/api/crm/deals", json={"name": n, "phone": p, "value": v, "source": "whatsapp", "note": "Asked about a chimney"})
            assert r.status_code == 200, r.text
        r = await c.post("/api/inquiries/whatsapp", json={"name": "Meera Joshi", "phone": "98201 10007",
                                                          "message": "Hi, we need a chimney and a hob for our new 3BHK. Can someone visit?"})
        assert r.status_code == 200, r.text
        r = await c.post("/api/call-coach/members", json={"name": "Invented Salesperson"})
        assert r.status_code == 200, r.text
        from app import lead_research
        from app.routers import inquiries
        await inquiries.settle()
        await lead_research.settle()
        print("seeded", uid)


asyncio.run(main())
