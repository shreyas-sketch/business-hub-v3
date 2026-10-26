"""v2.3: Connections show categories (CRM, Payment gateway, AI voice agent, Accounting software, Calendar, WhatsApp tool), not the
software companies behind them — a company's name only shows as the choice inside its picker. And the pipeline board's cards
carry how many notes, calls and meetings each opportunity has. Every business, person and number here is invented."""
import re

from app import connections
from tests.v23_helpers import v23_member

BRANDS = re.compile(r"\b(aisensy|employz|elevenlabs|tally|razorpay|google|twilio|wati|interakt)\b", re.I)
CATEGORIES = {"whatsapp": "WhatsApp tool", "employz": "CRM", "voice": "AI voice agent", "tally": "Accounting software",
              "razorpay": "Payment gateway", "calendar": "Calendar"}


async def test_connections_are_named_by_category_and_the_one_service_is_already_picked(client):
    owner = {"_id": "x", "tier": "lite"}
    # the WhatsApp tool's own fields (e.g. Wati's API address) only show after that service is picked, so they may name it
    shared = set.intersection(*(set(f) for f in connections.WHATSAPP_FIELDS.values()))
    for kind, name in CATEGORIES.items():
        v = connections.view(kind, None, owner)
        assert v["label"].startswith(name), (kind, v["label"])
        shown = [f for f in v["fields"] if kind != "whatsapp" or f["key"] in shared]
        words = " ".join([v["label"]] + [f"{f['label']} {f['help']}" for f in shown])
        assert not BRANDS.search(words), (kind, BRANDS.search(words).group(0))
    # where only one service works so far, it shows inside the picker and is already chosen
    crm = connections.view("employz", None, owner)
    pick = next(f for f in crm["fields"] if f["key"] == "provider")
    assert pick["value"] == "employz" and crm["services"] == {"employz": "Employz.ai"}
    gateway = next(f for f in connections.view("razorpay", None, owner)["fields"] if f["key"] == "provider")
    assert gateway["value"] == "razorpay"
    # a picked service alone doesn't count as connected
    assert not connections.ready("employz", {"provider": "employz"})


async def test_board_cards_count_the_notes_calls_and_meetings(client):
    c = await v23_member("9800100091")
    r = await c.post("/api/inquiries/whatsapp", json={"name": "Om Sai Fabricators", "phone": "98200 33333", "message": "Need 40 steel racks"})
    deal_id = r.json()["id"]
    await c.patch(f"/api/crm/deals/{deal_id}", json={"note": "Wants delivery before Diwali"})
    await c.patch(f"/api/crm/deals/{deal_id}", json={"note": "Asked for a GST invoice"})
    contact = (await c.get(f"/api/crm/deals/{deal_id}")).json()["contact"]
    r = await c.post(f"/api/crm/contacts/{contact['id']}/activities", json={"kind": "call", "text": "Called, sending the quote today", "lead_id": deal_id})
    assert r.status_code == 200, r.text
    deal = next(d for d in (await c.get("/api/crm/deals")).json()["deals"] if d["id"] == deal_id)
    assert deal["counts"]["note"] == 2 and deal["counts"]["call"] == 1 and deal["counts"]["meeting"] == 0
    assert deal["counts"]["all"] >= 3          # "created" and stage moves are history too, but not on the card's note count
