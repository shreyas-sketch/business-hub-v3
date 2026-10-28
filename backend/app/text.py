"""Small text helpers shared by the AI drafts and WhatsApp messages."""

TITLES = {"mr", "mrs", "ms", "miss", "dr", "shri", "shree", "smt", "sri", "kumari", "prof", "er", "adv", "ca"}
# names the hub writes itself when it doesn't know the person's name — never said to the customer ("Hello WhatsApp,")
PLACEHOLDER_NAMES = {"customer", "whatsapp customer"}


def greeting_name(name: str) -> str:
    """How to address someone in a message: 'Mrs. Kulkarni' stays whole, 'Neha Joshi' becomes 'Neha'. A placeholder name
    ("WhatsApp customer") gives "", so the message uses its no-name greeting."""
    parts = (name or "").split()
    if not parts or " ".join(parts).lower() in PLACEHOLDER_NAMES:
        return ""
    if parts[0].rstrip(".").lower() in TITLES:
        return " ".join(parts[:2]) if len(parts) > 1 else ""
    return parts[0]
