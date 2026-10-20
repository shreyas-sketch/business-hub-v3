"""
Brand kit (v2.1, 3.11): the message system, the design system and the designs, kept in `brand_kits` {_id: ws}.

messages  core / website / marketing / sales / social / voice, generated from the Business Brain (one AI run) and editable
          item by item. `generated` keeps the last AI version and `edited` marks what the owner changed, so a Knowledge Hub
          change refreshes only what the owner hasn't touched. The tone of voice is copied into the Brain, where every AI
          writer reads it.
design    colours (primary / secondary / accent, from the logo, the website or picked, with checked text contrast), a heading
          and a body font from a curated list (Devanagari where needed), the logo (or a text logo), corner style and spacing.
          Saved as tokens and applied to the website, the launch kit, invoices and quotations, and lead magnet covers.
"""
import colorsys
import copy
import io
import re

from PIL import Image

MESSAGE_SHAPE = {
    "core": {"one_line": str, "pitch": str, "positioning": {"who": str, "what": str, "why": str}, "proof": [str]},
    "website": {"headline": str, "subheadline": str, "about": str, "ctas": [str]},
    "marketing": {"ad_headlines": [str], "festival_line": str, "tagline": str},
    "sales": {"elevator": str, "whatsapp_intro": str, "proposal_opener": str, "email_signature": str},
    "social": {"instagram": str, "linkedin": str, "facebook": str, "whatsapp_business": str, "hooks": [str]},
    "voice": {"tone": str, "dos": [str], "donts": [str], "use": [str], "avoid": [str]},
}
LIST_MAX = {"proof": 3, "ctas": 3, "ad_headlines": 5, "hooks": 5, "dos": 5, "donts": 5, "use": 10, "avoid": 10}
TEXT_MAX = {"pitch": 700, "about": 900, "elevator": 700, "whatsapp_intro": 600, "proposal_opener": 500, "email_signature": 300,
            "linkedin": 300, "facebook": 300, "instagram": 150, "whatsapp_business": 139}
SECTION_LABELS = {"core": "Core message", "website": "Website", "marketing": "Marketing", "sales": "Sales", "social": "Social media",
                  "voice": "Tone of voice"}

# heading/body fonts the designs can draw: (key, label, file stem, Devanagari?)
FONTS = [("poppins", "Poppins", "Poppins", True), ("mukta", "Mukta", "Mukta", True), ("hind", "Hind", "Hind", True),
         ("baloo2", "Baloo 2", "Baloo2", True), ("martel", "Martel (serif)", "Martel", True), ("yatra", "Yatra One (display)", "YatraOne", True),
         ("playfair", "Playfair Display (serif, English only)", "PlayfairDisplay", False), ("inter", "Inter (English only)", "Inter", False),
         ("noto", "Noto Sans Devanagari", "NotoSansDevanagari", True)]
FONT_KEYS = [f[0] for f in FONTS]
CORNERS = ("square", "soft", "round")
SPACING = ("compact", "comfortable")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _clean(value, shape):
    if isinstance(shape, dict):
        value = value if isinstance(value, dict) else {}
        return {k: _clean(value.get(k), s) for k, s in shape.items()}
    if isinstance(shape, list):
        items = [re.sub(r"\s+", " ", str(x)).strip() for x in (value if isinstance(value, list) else []) if str(x or "").strip()]
        return items
    return str(value or "").strip()


def shape_messages(raw: dict) -> dict:
    """AI or owner messages → the fixed shape, with lengths kept in check. Raises when the core is missing."""
    out = _clean(raw if isinstance(raw, dict) else {}, MESSAGE_SHAPE)
    for sec in out.values():
        for k, v in list(sec.items()):
            if isinstance(v, list):
                sec[k] = [x[:200] for x in v][:LIST_MAX.get(k, 10)]
            elif isinstance(v, str):
                sec[k] = v[:TEXT_MAX.get(k, 240)]
            elif isinstance(v, dict):
                sec[k] = {a: b[:240] for a, b in v.items()}
    if not out["core"]["one_line"] or not out["voice"]["tone"]:
        raise ValueError("the core message or the tone is missing")
    return out


def paths(messages: dict) -> dict[str, object]:
    """Every editable item by its path: core.one_line, core.positioning.who, marketing.ad_headlines …"""
    out = {}
    for sec, items in messages.items():
        for k, v in items.items():
            if isinstance(v, dict):
                for a, b in v.items():
                    out[f"{sec}.{k}.{a}"] = b
            else:
                out[f"{sec}.{k}"] = v
    return out


def set_path(messages: dict, path: str, value) -> None:
    parts = path.split(".")
    node = messages
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def merge_refresh(current: dict, generated_new: dict, edited: dict) -> dict:
    """A refresh: new AI wording everywhere the owner hasn't edited."""
    out = copy.deepcopy(current)
    for path, value in paths(generated_new).items():
        if not edited.get(path):
            set_path(out, path, value)
    return out


# ───────────────────────── colours ─────────────────────────
def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def text_on(bg: str) -> str:
    """White or near-black, whichever reads better on this colour."""
    return "#FFFFFF" if contrast(bg, "#FFFFFF") >= contrast(bg, "#14171C") else "#14171C"


def checked(colors: dict) -> dict:
    """The palette with the text colour for each, and whether it passes WCAG AA (4.5:1) for normal text."""
    out = {}
    for k in ("primary", "secondary", "accent"):
        c = colors.get(k)
        if c:
            on = text_on(c)
            ratio = contrast(c, on)
            out[k] = {"color": c.upper(), "text": on, "ratio": ratio, "passes": ratio >= 4.5}
    bg = colors.get("background") or "#FFFFFF"
    out["primary_on_background"] = {"ratio": contrast(colors.get("primary") or "#1F4E79", bg), "passes": contrast(colors.get("primary") or "#1F4E79", bg) >= 3}
    return out


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(x) for x in rgb[:3])


MAX_PIXELS = 25_000_000   # a 2 MB PNG can still be 100+ megapixels; a logo never needs more than 5000 × 5000


def open_image(data: bytes) -> Image.Image:
    """Opens an uploaded image, refusing one too large to decode safely (checked before any pixel is decoded)."""
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise ValueError(f"image too large: {w}×{h}")
    return img


def from_image(data: bytes) -> dict:
    """Primary, secondary and accent from a logo: the most used strong colours, ignoring white, black and greys."""
    img = open_image(data).convert("RGBA")
    img.thumbnail((160, 160))
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    pal = img.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    counts = sorted(pal.getcolors() or [], reverse=True)
    palette = pal.getpalette()
    strong = []
    for n, idx in counts:
        rgb = palette[idx * 3: idx * 3 + 3]
        h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in rgb))
        if s < 0.25 or l < 0.08 or l > 0.92:
            continue
        hexc = _hex(rgb)
        if all(abs(h - colorsys.rgb_to_hls(*(int(o[i:i + 2], 16) / 255 for i in (1, 3, 5)))[0]) > 0.04 for o in strong):
            strong.append(hexc)
    strong = strong or ["#1F4E79"]
    return {"primary": strong[0], "secondary": strong[1] if len(strong) > 1 else partner(strong[0]), "accent": strong[2] if len(strong) > 2 else "#F2A93B"}


def partner(hex_color: str) -> str:
    """A second colour that goes with the first: a deeper shade of a light colour, a lighter tint of a dark one —
    never plain black or white."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    _h, l, _s = colorsys.rgb_to_hls(r, g, b)
    return shade(hex_color, -min(0.22, l * 0.45)) if l >= 0.45 else shade(hex_color, min(0.25, (1 - l) * 0.4))


def shade(hex_color: str, amount: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.0, min(1.0, l + amount))
    return _hex([round(x * 255) for x in colorsys.hls_to_rgb(h, l, s)])


def default_design(name: str, site_colour: str | None = None) -> dict:
    primary = site_colour if site_colour and HEX.match(site_colour) else "#1F4E79"
    return {"colors": {"primary": primary.upper(), "secondary": partner(primary), "accent": "#F2A93B", "background": "#FFFFFF", "text": "#14171C"},
            "fonts": {"heading": "poppins", "body": "mukta"}, "logo_file_id": None, "text_logo": name[:40], "corners": "soft",
            "spacing": "comfortable", "source": "website" if site_colour else "default"}
