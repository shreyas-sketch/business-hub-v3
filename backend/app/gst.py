"""GST slabs in one place. GST 2.0 (from 22 September 2025) moved goods and services to 5% and 18%, with 40% for luxury and
sin goods; 12% and 28% are no longer offered for new entries. They stay accepted, so older quotations, invoices and saved
settings (and items still taxed at 28%) keep working."""

RATES = (0, 0.25, 3, 5, 18, 40)                  # offered when choosing a rate
ACCEPTED = (0, 0.25, 3, 5, 12, 18, 28, 40)       # valid on anything saved
MAX = 40


def offered(current: float | None = None) -> list[float]:
    """The rates to choose from, plus the one already saved when it's an older slab, so a select never loses it."""
    out = list(RATES)
    if current is not None and current in ACCEPTED and current not in out:
        out = sorted([*out, current])
    return out
