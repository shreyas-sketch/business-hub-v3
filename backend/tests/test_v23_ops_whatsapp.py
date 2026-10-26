"""v2.3 (OwnerDesk): the compliance calendar's GST and TAN pop-up, and the "WhatsApp tool" (AiSensy, Wati, Interakt or Meta
behind one neutral name). Wati, Interakt and Meta-direct are checked against fake servers only. Every business, person, GST
number, TAN and phone number here is invented."""
import json

import httpx

from app import connections, integrations, messaging
from app.compliance_rules import TDS_KEYS
from app.config import settings
from app.db import db
from app.event import gstin_check_char
from app.knowledge import settle
from app.routers.compliance import TAN_NOTE
from tests.conftest import new_owner, PROFILE
from tests.test_integrations import FakeHTTP
from tests.v23_helpers import owner_id, v23_member


def gstin(first14: str) -> str:
    return first14 + gstin_check_char(first14)


COMPANY_GST = gstin("27AAACK4821M1Z")     # PAN's 4th letter C: a company (Maharashtra)
PROPRIETOR_GST = gstin("24ABRPK7310Q1Z")  # P: an individual — a proprietorship (Gujarat)


async def compliance(c) -> dict:
    r = await c.get("/api/compliance?days=400")
    assert r.status_code == 200, r.text
    return r.json()


def rule_keys(d: dict) -> set:
    return {i["rule_key"] for i in d["all"]}


# ───────────────────────── the pop-up ─────────────────────────
async def test_popup_asks_on_first_open_and_a_company_with_a_tan_gets_gst_and_tds(client):
    c = await v23_member("9811100001")
    d = await compliance(c)
    assert d["setup"]["needed"] and d["setup"]["popup"] and not d["setup"]["banner"] and d["setup"]["ask_staff"]
    assert not d["all"]                                   # nothing to show until the business is known

    g = (await c.post("/api/compliance/setup/gstin", json={"gstin": COMPANY_GST.lower()})).json()
    assert g["valid"] and g["state"] == "Maharashtra" and g["pan_kind"] == "Company" and g["ask_entity"]
    bad = (await c.post("/api/compliance/setup/gstin", json={"gstin": COMPANY_GST[:-1] + ("A" if COMPANY_GST[-1] != "A" else "B")})).json()
    assert not bad["valid"] and "add up" in bad["error"]

    # a company's GST number fits several business types: the pop-up asks which
    r = await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": COMPANY_GST, "has_tan": True, "tan": "MUMK48210B", "staff": 12})
    assert r.status_code == 400 and "Pick which one" in r.json()["detail"]
    r = await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": COMPANY_GST, "has_tan": True, "tan": "mumk 48210b",
                                                     "entity": "pvt_ltd", "staff": 12})
    assert r.status_code == 200, r.text
    assert not r.json()["setup"]["needed"] and r.json()["notes"] == []
    b = await db().businesses.find_one({"owner_id": await owner_id(c)})
    ident = b["identity"]
    assert ident["gstin"] == COMPANY_GST and ident["pan"] == COMPANY_GST[2:12] and ident["state"] == "Maharashtra"
    assert ident["entity"] == "pvt_ltd" and ident["has_tan"] is True and ident["tan"] == "MUMK48210B" and ident["staff"] == 12
    d = await compliance(c)
    keys = rule_keys(d)
    assert {"gstr1", "gstr3b", *TDS_KEYS, "aoc4", "mgt7"} <= keys and not d["setup"]["needed"]
    assert d["label"] == "Dates come from the rules table, not from AI."


async def test_a_tan_decides_tds_once_answered(client):
    # a proprietor with a TAN deducts TDS (before v2.3 the TDS dates only followed the business type)
    p = await v23_member("9811100002")
    r = await p.post("/api/compliance/setup", json={"gst_registered": True, "gstin": PROPRIETOR_GST, "has_tan": True, "tan": "AHMK73100Q", "staff": 4})
    assert r.status_code == 200, r.text
    keys = rule_keys(await compliance(p))
    assert set(TDS_KEYS) <= keys and "itr_july" in keys and "gstr3b" in keys

    # a company that says "No TAN": no TDS dates, and a clear note to check with the CA
    c = await v23_member("9811100003")
    r = await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": COMPANY_GST, "entity": "pvt_ltd", "has_tan": False, "staff": 25})
    assert r.status_code == 200 and r.json()["notes"] == [TAN_NOTE]
    d = await compliance(c)
    assert not set(TDS_KEYS) & rule_keys(d) and d["notes"] == [TAN_NOTE] and "aoc4" in rule_keys(d)

    # not GST registered: the state and business type are asked instead; still no GST filings
    n = await v23_member("9811100004")
    r = await n.post("/api/compliance/setup", json={"gst_registered": False, "has_tan": False, "staff": 2})
    assert r.status_code == 400 and "business type" in r.json()["detail"]
    r = await n.post("/api/compliance/setup", json={"gst_registered": False, "has_tan": False, "staff": 2, "entity": "proprietor", "state": "Atlantis"})
    assert r.status_code == 400 and "state" in r.json()["detail"]
    r = await n.post("/api/compliance/setup", json={"gst_registered": False, "has_tan": False, "staff": 2, "entity": "proprietor", "state": "Karnataka"})
    assert r.status_code == 200, r.text
    keys = rule_keys(await compliance(n))
    assert keys and not [k for k in keys if k.startswith("gst") or k in TDS_KEYS]


async def test_wrong_numbers_are_refused_with_plain_words(client):
    c = await v23_member("9811100005")
    r = await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": "27ABCDE1234", "has_tan": False, "staff": 1})
    assert r.status_code == 400 and "15 characters" in r.json()["detail"]
    r = await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": PROPRIETOR_GST, "has_tan": True, "tan": "12345", "staff": 1})
    assert r.status_code == 400 and "MUMA12345B" in r.json()["detail"]
    r = await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": PROPRIETOR_GST, "has_tan": False})
    assert r.status_code == 400 and "How many people" in r.json()["detail"]
    assert (await db().businesses.find_one({"owner_id": await owner_id(c)})).get("identity") is None   # nothing half-saved


async def test_skip_shows_a_banner_until_filled_and_the_knowledge_hub_keeps_the_tan(client):
    c = await v23_member("9811100006")
    s = (await c.post("/api/compliance/setup/skip")).json()
    assert s["needed"] and not s["popup"] and s["banner"]
    d = await compliance(c)
    assert d["setup"]["banner"] and not d["setup"]["popup"]
    assert (await c.post("/api/compliance/setup", json={"gst_registered": True, "gstin": PROPRIETOR_GST, "has_tan": True, "tan": "AHMK73100Q",
                                                         "staff": 3})).status_code == 200
    d = await compliance(c)
    assert not d["setup"]["needed"] and not d["setup"]["banner"]
    # the Knowledge Hub's identity form doesn't carry the TAN; saving it keeps the answer
    r = await c.put("/api/knowledge/identity", json={"entity": "proprietor", "gst_registered": True, "gstin": PROPRIETOR_GST, "gst_scheme": "qrmp",
                                                      "state": "Gujarat", "staff": 5, "registrations": []})
    assert r.status_code == 200, r.text
    await settle()
    ident = (await db().businesses.find_one({"owner_id": await owner_id(c)}))["identity"]
    assert ident["has_tan"] is True and ident["tan"] == "AHMK73100Q" and ident["gst_scheme"] == "qrmp" and ident["pan"] == PROPRIETOR_GST[2:12]
    assert set(TDS_KEYS) <= rule_keys(await compliance(c))


async def test_identity_without_a_tan_answer_keeps_tds_by_business_type(client):
    """Owners who never saw the pop-up keep today's rule: a company gets the TDS dates, a proprietor doesn't."""
    c = await v23_member("9811100007")
    assert (await c.put("/api/knowledge/identity", json={"entity": "llp", "gst_registered": False, "state": "Maharashtra", "staff": 6,
                                                          "registrations": []})).status_code == 200
    await settle()
    d = await compliance(c)
    assert set(TDS_KEYS) <= rule_keys(d) and d["setup"]["needed"] and d["notes"] == []


async def test_only_the_owner_saves_and_a_free_owner_is_locked(client):
    free = await v23_member("9811100008", paid=False)
    assert (await free.get("/api/compliance")).status_code == 403
    assert (await free.post("/api/compliance/setup", json={"gst_registered": False, "has_tan": False})).status_code == 403


# ───────────────────────── the hub's own number, through any WhatsApp tool ─────────────────────────
class Tool:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            object.__setattr__(settings, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            object.__setattr__(settings, k, v)


async def test_wati_sends_the_hubs_templates(monkeypatch):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json={"result": True, "id": "wati-1"})
    monkeypatch.setattr(messaging.httpx, "AsyncClient", FakeHTTP(handler))
    with Tool(whatsapp_provider="wati", wati_api_url="https://live-mt-server.example/30001", wati_api_token="tok-1", aisensy_otp_campaign="hub_otp"):
        assert messaging.hub_ready()
        r = await messaging.whatsapp_template("+919820000011", "lead_alert", "Asha", ["Shree Ganesh", "Ravi\n\nJoshi", ""], "lead_alert")
        assert r == {"sent": True, "via": "whatsapp", "id": "wati-1"}
        req = sent[0]
        assert req.url.path == "/30001/api/v1/sendTemplateMessage" and req.url.params["whatsappNumber"] == "919820000011"
        assert req.headers["authorization"] == "Bearer tok-1"
        assert json.loads(req.content) == {"template_name": "lead_alert", "broadcast_name": "hub-lead_alert",
                                           "parameters": [{"name": "1", "value": "Shree Ganesh"}, {"name": "2", "value": "Ravi Joshi"},
                                                          {"name": "3", "value": "-"}]}
        assert await messaging.send_otp("+919820000011", "482913") == {"sent": True, "via": "whatsapp", "id": "wati-1"}
        assert json.loads(sent[1].content)["template_name"] == "hub_otp"


async def test_interakt_and_meta_send_the_hubs_templates_and_a_refusal_is_not_a_send(monkeypatch):
    sent, answer = [], {"r": httpx.Response(201, json={"result": True, "id": "ik-9"})}

    def handler(req):
        sent.append(req)
        return answer["r"]
    monkeypatch.setattr(messaging.httpx, "AsyncClient", FakeHTTP(handler))
    with Tool(whatsapp_provider="interakt", interakt_api_key="c2VjcmV0", aisensy_otp_campaign="hub_otp"):
        r = await messaging.send_otp("+919820000012", "771205")
        assert r == {"sent": True, "via": "whatsapp", "id": "ik-9"}
        assert str(sent[0].url) == settings.interakt_api_url and sent[0].headers["authorization"] == "Basic c2VjcmV0"
        assert json.loads(sent[0].content) == {"countryCode": "+91", "phoneNumber": "9820000012", "type": "Template", "callbackData": "otp",
                                               "template": {"name": "hub_otp", "languageCode": "en", "bodyValues": ["771205"],
                                                            "buttonValues": {"0": ["771205"]}}}
        await messaging.whatsapp_template("+919820000012", "day1_report", "Asha", ["Asha", "Your report"], "day1_report",
                                          media={"url": "https://hub.example.in/r/m/k1.png", "filename": "report"})
        tpl = json.loads(sent[1].content)["template"]
        assert tpl["headerValues"] == ["https://hub.example.in/r/m/k1.png"] and tpl["fileName"] == "report"
        answer["r"] = httpx.Response(200, json={"result": False, "message": "Template not approved"})
        assert (await messaging.whatsapp_template("+919820000012", "x", "", ["a"], "owner_alert"))["sent"] is False
    answer["r"] = httpx.Response(200, json={"messages": [{"id": "wamid.H1"}]})
    with Tool(whatsapp_provider="meta", whatsapp_hub_phone_number_id="PN77", whatsapp_hub_access_token="EAAT"):
        r = await messaging.whatsapp_template("+919820000013", "owner_alert", "", ["Shree Ganesh", "Hello"], "owner_alert")
        assert r == {"sent": True, "via": "whatsapp", "id": "wamid.H1"}
        req = sent[-1]
        assert req.url.path.endswith("/PN77/messages") and req.headers["authorization"] == "Bearer EAAT"
        body = json.loads(req.content)
        assert body["to"] == "919820000013" and body["template"]["name"] == "owner_alert"
        assert body["template"]["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Shree Ganesh"}, {"type": "text", "text": "Hello"}]}]
    with Tool(whatsapp_provider="wati", wati_api_url="", wati_api_token=""):
        assert not messaging.hub_ready()


# ───────────────────────── an owner's own number ─────────────────────────
async def growth_owner(phone: str):
    c = await new_owner(phone, name="Owner Test")
    assert (await c.put("/api/business", json=PROFILE)).status_code == 200
    uid = await owner_id(c)
    await db().users.update_one({"_id": uid}, {"$set": {"plan": "growth"}})
    return c, uid


async def test_the_whatsapp_tool_card_is_neutral_and_saves_wati(client):
    c, uid = await growth_owner("9811100021")
    wa = next(x for x in (await c.get("/api/connections")).json() if x["kind"] == "whatsapp")
    assert wa["label"] == "WhatsApp tool — your own number"
    assert set(wa["services"]) >= {"aisensy", "wati", "interakt", "meta"} and wa["service_fields"]["wati"][:4] == ["provider", "number", "api_url", "api_token"]
    assert not [f for f in wa["fields"] if "aisensy" in (f["label"] + f["help"]).lower()]   # only the picker names the service
    r = await c.put("/api/connections/whatsapp", json={"values": {"provider": "wati", "number": "98200 30001", "api_url": "https://live-mt-server.example/30001",
                                                                   "api_token": "wati-secret", "tpl_instant": "instant_v1"}})
    assert r.status_code == 200 and r.json()["connected"], r.text
    raw = (await db().connections.find_one({"_id": uid}))["whatsapp"]
    assert raw["api_token"] != "wati-secret" and (await connections.get(uid, "whatsapp"))["api_token"] == "wati-secret"
    assert (await c.put("/api/connections/whatsapp", json={"values": {"provider": "carrier-pigeon"}})).status_code == 400


async def test_wati_and_interakt_owner_messages_and_replies(monkeypatch):
    seen = []

    def fake(req):
        seen.append(req)
        return httpx.Response(200, json={"result": True, "id": "m-1"})
    monkeypatch.setattr(integrations, "TRANSPORT", httpx.MockTransport(fake))
    wati = {"provider": "wati", "api_url": "https://live-mt-server.example/30001/", "api_token": "Bearer wt"}
    r = await integrations.wa_template("ws", wati, "98200 00031", "followup_v1", ["Neha", "Shree Ganesh", "Hello  there\n"], "Neha", "followup")
    assert r["sent"] and r["id"] == "m-1"
    assert seen[0].url.path == "/30001/api/v1/sendTemplateMessage" and seen[0].url.params["whatsappNumber"] == "919820000031"
    assert seen[0].headers["authorization"] == "Bearer wt"
    assert json.loads(seen[0].content)["parameters"][2] == {"name": "3", "value": "Hello there"}
    await integrations.wa_text("ws", wati, "+91 98200 00031", "Thanks!")
    assert seen[1].url.path == "/30001/api/v1/sendSessionMessage/919820000031" and seen[1].url.params["messageText"] == "Thanks!"

    ik = {"provider": "interakt", "api_key": "aWs=", "language": "hi"}
    await integrations.wa_template("ws", ik, "9820000032", "review_v1", ["Neha"], "", "review")
    body = json.loads(seen[2].content)
    assert seen[2].headers["authorization"] == "Basic aWs=" and body["countryCode"] == "+91" and body["phoneNumber"] == "9820000032"
    assert body["template"] == {"name": "review_v1", "languageCode": "hi", "bodyValues": ["Neha"]}
    await integrations.wa_text("ws", ik, "9820000032", "On our way")
    assert json.loads(seen[3].content) == {"countryCode": "+91", "phoneNumber": "9820000032", "type": "Text", "data": {"message": "On our way"}}
    assert connections.ready("whatsapp", wati) and connections.ready("whatsapp", ik) and not connections.ready("whatsapp", {"provider": "wati", "api_url": "x"})

    monkeypatch.setattr(integrations, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(200, json={"result": False, "info": "Invalid"})))
    assert (await integrations.wa_template("ws", wati, "9820000031", "x", ["a"]))["sent"] is False


def test_incoming_wati_and_interakt_messages_are_read():
    wati = {"eventType": "message", "id": "6928", "whatsappMessageId": "wamid.W1", "waId": "919820000041", "senderName": "Neha Joshi",
            "text": "Is the kitchen price fixed?", "type": "text", "owner": False}
    assert integrations.parse_inbound(wati) == [{"from": "919820000041", "name": "Neha Joshi", "text": "Is the kitchen price fixed?",
                                                 "id": "wamid.W1", "type": "text"}]
    assert integrations.parse_inbound({**wati, "owner": True}) == []                    # the business's own message
    assert integrations.parse_inbound({**wati, "eventType": "templateMessageSent"}) == []
    ik = {"version": "1.0", "type": "message_received", "data": {
        "customer": {"id": "c1", "channel_phone_number": "919820000042", "traits": {"name": "Ravi Kulkarni"}},
        "message": {"id": "ik-m1", "chat_message_type": "CustomerMessage", "message_content_type": "Text", "message": "Site visit tomorrow?"}}}
    assert integrations.parse_inbound(ik) == [{"from": "919820000042", "name": "Ravi Kulkarni", "text": "Site visit tomorrow?", "id": "ik-m1", "type": "text"}]
    ik["data"]["message"]["chat_message_type"] = "AgentMessage"
    assert integrations.parse_inbound(ik) == []


async def test_a_wati_webhook_reaches_the_owners_chats(client, monkeypatch):
    c, uid = await growth_owner("9811100022")
    await c.put("/api/connections/whatsapp", json={"values": {"provider": "wati", "api_url": "https://live-mt-server.example/30002", "api_token": "t"}})
    token = (await db().connections.find_one({"_id": uid}))["whatsapp"]["webhook_token"]
    got = []

    async def inbound(ws, conn, msg):
        got.append((ws, conn["provider"], msg["from"], msg["text"]))
    monkeypatch.setattr(integrations, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(200, json={"result": True})))
    from app import chat_ai
    monkeypatch.setattr(chat_ai, "inbound", inbound)
    r = await client.post(f"/api/hooks/whatsapp/{token}", json={"eventType": "message", "waId": "919820000051", "senderName": "Neha", "text": "Hi",
                                                                "type": "text", "owner": False, "id": "x1"})
    assert r.status_code == 200 and r.json()["handled"] == 1 and got == [(uid, "wati", "919820000051", "Hi")]
    assert (await client.post("/api/hooks/whatsapp/not-a-real-token-0000", json={})).status_code == 404
