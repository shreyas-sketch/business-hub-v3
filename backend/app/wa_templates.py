"""Ready WhatsApp messages the owner can pick instead of writing with AI: no AI run, the same words every time, filled with
the customer's name and the business's own details. A part in [[ ]] is left out when a detail it needs is missing (no
website yet → no "see our work" line), so a message never goes out with a blank in it."""
import re

TEMPLATES = [
    # first reply
    ("thanks", "First reply", "Thank you for asking",
     "Hi {name}, thank you for reaching out to {business}![[ I'm {owner}.]] Could you tell me a little more about what you need, "
     "so I can share the right options and prices?"),
    ("busy_now", "First reply", "Will reply shortly",
     "Hi {name}, thanks for your message. I'm with a customer right now and will call or message you back within the hour. — [[{owner}, ]]{business}"),
    ("need_details", "First reply", "Ask for details",
     "Hi {name}, happy to help! To give you an exact quote, could you share: 1) what you need, 2) quantity or size, "
     "3) your location, and 4) when you need it by?"),
    ("best_time", "First reply", "Best time to call",
     "Hi {name}, thanks for contacting {business}. What's a good time to call you today? A 5-minute call will help me understand exactly what you need."),
    ("intro", "First reply", "About us",
     "Hi {name}, this is {business}[[ in {city}]][[ — {owner} here]]. We'd be glad to help you.[[ You can see our work here: {website}]] "
     "Tell me what you're looking for and I'll guide you."),
    # price and quote
    ("price_range", "Price & quote", "Price depends on details",
     "Hi {name}, the price depends on the exact requirement. Share a few details (size, quantity, finish) and I'll send you a "
     "clear quote today, with no hidden charges."),
    ("quote_sent", "Price & quote", "Quote is on the way",
     "Hi {name}, I'm preparing your quote now and will send it within a few hours. If anything changes in your requirement, just tell me here."),
    ("quote_check", "Price & quote", "Did you see the quote?",
     "Hi {name}, just checking whether you had a chance to look at the quote I sent. Happy to explain anything or adjust it to your budget."),
    ("offer", "Price & quote", "Special offer",
     "Hi {name}, good news: we have a special offer running this month at {business}. Shall I share the details and what it would cost for you?"),
    ("payment", "Price & quote", "Payment options",
     "Hi {name}, you can pay by UPI, bank transfer or card. A small advance confirms your booking and the rest is due on delivery. "
     "Shall I send the payment details?"),
    # visit and meeting
    ("visit_us", "Visit & meeting", "Invite to visit",
     "Hi {name}, you're welcome to visit us[[ in {city}]] and see everything in person. When would suit you? I'll make sure I'm there to meet you."),
    ("site_visit", "Visit & meeting", "Offer a site visit",
     "Hi {name}, we can visit your place to understand the requirement and take measurements. Which day and time works for you?"),
    ("confirm_visit", "Visit & meeting", "Confirm the appointment",
     "Hi {name}, this is to confirm our appointment. Please reply with the date and time that suits you, and I'll block it for you. — {business}"),
    ("location", "Visit & meeting", "Share location",
     "Hi {name}, here's how to reach us: {business}[[, {city}]]. Reply here if you'd like me to send the map pin, or call when you're nearby."),
    # follow-up
    ("follow_1", "Follow-up", "Gentle follow-up",
     "Hi {name}, just following up on your inquiry with {business}. Are you still looking for this? I'd be happy to help whenever you're ready."),
    ("follow_2", "Follow-up", "Any questions?",
     "Hi {name}, do you have any questions I can answer? Many customers ask about timelines and warranty. I'm happy to explain."),
    ("follow_last", "Follow-up", "Last check-in",
     "Hi {name}, I won't keep messaging you. If you need us later, just reply here and we'll pick up from where we left off. Thank you! — [[{owner}, ]]{business}"),
    # after the sale
    ("thanks_order", "After the sale", "Thanks for the order",
     "Hi {name}, thank you for choosing {business}! Your order is confirmed. I'll keep you updated at every step."),
    ("review", "After the sale", "Ask for a review",
     "Hi {name}, thank you for your trust in {business}. If you're happy with our work, a short review would mean a lot to us. Thank you!"),
    ("referral", "After the sale", "Ask for a referral",
     "Hi {name}, it was a pleasure working with you. If a friend or family member needs something similar, please share our number. "
     "We'll take good care of them.[[ {website}]]"),
]
GROUPS = list(dict.fromkeys(g for _, g, _, _ in TEMPLATES))
_OPTIONAL = re.compile(r"\[\[(.*?)\]\]")
_FIELD = re.compile(r"\{(\w+)\}")


def fill(text: str, values: dict) -> str:
    def part(m):
        inner = m.group(1)
        return inner if all(values.get(k) for k in _FIELD.findall(inner)) else ""
    out = _OPTIONAL.sub(part, text)
    out = _FIELD.sub(lambda m: str(values.get(m.group(1)) or ""), out)
    return re.sub(r" {2,}", " ", out).replace(" .", ".").replace(" ,", ",").strip()


def filled(values: dict) -> list[dict]:
    v = {**values, "name": values.get("name") or "there"}
    return [{"id": k, "group": g, "title": t, "text": fill(x, v)} for k, g, t, x in TEMPLATES]


IDS = {k for k, *_ in TEMPLATES}
