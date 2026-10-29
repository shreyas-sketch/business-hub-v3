"""Fixes found while testing the Brand and Data engines live: a long business name fits its box, the social post's button
stays clear of the text and the footer, a thin profile's starter posts never repeat, bank payments with no payee get a
name of their own, and GST needs the vendor's GST number to be claimable. No database."""
from datetime import date

import pytest
from fastapi import HTTPException

from app import bankstatement as bs, brand_kit, designs
from app.routers import expenses as ex
from app.routers.socialmedia import starter_posts


class Spy(designs.Canvas):
    """Records every line drawn, with its box, so the layout can be checked without looking at pixels."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.drawn = []
        real = self.draw.text

        def text(xy, line, font=None, fill=None):
            l, t, r, b = self.draw.textbbox(xy, line, font=font)
            self.drawn.append((line, l, t, r, b))
            return real(xy, line, font=font, fill=fill)
        self.draw.text = text


def render(key, fields, name, monkeypatch):
    made = []
    monkeypatch.setattr(designs, "Canvas", lambda *a, **k: made.append(Spy(*a, **k)) or made[-1])
    designs.render(key, fields, brand_kit.default_design(name), {"name": name, "whatsapp": "98200 12345"})
    return made[0]


@pytest.mark.parametrize("key", ["visiting_card", "email_header"])
def test_a_long_business_name_shrinks_to_its_box(key, monkeypatch):
    fields = {"tagline": "Brand design that small businesses can afford"} if key == "email_header" else {"person": "Asha", "role": "Founder"}
    c = render(key, fields, "Shreeganeshenterprises", monkeypatch)
    for _, l, _, r, _ in c.drawn:
        assert 0 <= l and r <= c.W, "a line runs off the design"
    if key == "email_header":   # the name and the tagline don't overlap
        name = next(x for x in c.drawn if x[0] == "Shreeganeshenterprises")
        tag = [x for x in c.drawn if x[0] != "Shreeganeshenterprises"]
        assert all(name[3] <= t[1] or t[3] <= name[1] or name[4] <= t[2] or t[4] <= name[2] for t in tag)


def test_the_social_button_stays_clear_of_the_text_and_the_footer(monkeypatch):
    long = ("We design logos, brand guidelines, packaging and print for small businesses in Thane and Mumbai, so they look as "
            "good as the big names — fast, fair and with two rounds of changes included.")
    c = render("social_square", {"headline": "Brand design that works as hard as you do", "body": long, "cta": "Message us"},
               "DesignCoke", monkeypatch)
    button = next(x for x in c.drawn if x[0] == "Message us")
    footer = next(x for x in c.drawn if x[0].startswith("DesignCoke ·"))
    body = [x for x in c.drawn if x not in (button, footer) and x[2] > c.H * 0.2]
    pill_top = button[2] - c.W * 0.036   # the pill reaches a little above its text
    assert all(b[4] <= pill_top for b in body), "the body runs under the button"
    assert button[4] < footer[2], "the button touches the footer"


def test_a_thin_profile_never_gets_the_same_starter_post_twice():
    for p in ({"name": "DesignCoke", "industry": "Graphic design studio"},
              {"name": "DesignCoke", "industry": "Graphic design studio", "city": "Thane"}):
        out = starter_posts(p, {}, {}, date(2026, 10, 29))
        seen = [(x["template"], tuple(sorted(x["fields"].items()))) for x in out]
        assert out and len(seen) == len(set(seen))


def test_a_payment_with_no_payee_is_named_from_its_narration():
    assert bs.payee_label("BILLPAY MSEDCL ELECTRICITY XXXXXXXX6789") == "Billpay MSEDCL Electricity"
    assert bs.payee_label("GST PAYMENT CIN XXXXXXXXXX7890 GSTIN 27ABCDE1234F1Z5") == "GST Payment"
    assert bs.payee_label("SMS CHARGES") == "SMS Charges"
    assert bs.payee_label("REFUND TO r***@gmail.com MOB XXXXXX3210") == "Refund"
    assert bs.payee_label("0001234 5678") == ""


def test_gst_is_claimable_only_with_the_vendors_gst_number():
    with_no = {"gst_claimable": True, "gst": 180000, "amount": 1180000, "vendor_gstin": ""}
    assert ex._claimable({**with_no, "vendor_gstin": "27ABCDE1234F1Z5"}) and not ex._claimable(with_no)
    assert not ex._claimable({**with_no, "vendor_gstin": "27ABCDE1234F1Z5", "gst": 0})
    with pytest.raises(HTTPException) as e:
        ex._check(with_no, {"gst_claimable"}, set())
    assert "vendor's GST number" in e.value.detail
    ex._check({**with_no, "gst_claimable": False}, {"gst_claimable"}, set())   # no claim, no number needed
