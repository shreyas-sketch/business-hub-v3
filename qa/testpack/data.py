"""One invented business and everything it would upload or type into the hub. Every name, number, GSTIN, phone, account
and address here is invented. Dates are relative to TODAY so the pack stays current for about a month."""
import random
from datetime import date, timedelta

TODAY = date(2026, 10, 23)
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin(state: str, pan: str, entity: str = "1") -> str:
    first = f"{state}{pan}{entity}Z"
    total = 0
    for i, ch in enumerate(first):
        p = CHARSET.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return first + CHARSET[(36 - total % 36) % 36]


BIZ = {
    "name": "Deshmukh Modular Kitchens",
    "legal": "Deshmukh Modular Kitchens Private Limited",
    "owner": "Rahul Deshmukh",
    "city": "Pune",
    "address": "Shop 4, Sai Square, Baner Road, Baner, Pune 411045",
    "factory": "Plot C-17, MIDC Bhosari, Pune 411026",
    "industry": "Modular kitchens, wardrobes and full home interiors, made in our own factory in Bhosari",
    "offers": [
        {"name": "L-shaped modular kitchen", "price": "₹1,65,000", "unit": "onwards"},
        {"name": "Sliding wardrobe", "price": "₹1,450", "unit": "per sq ft"},
        {"name": "2BHK full interiors", "price": "₹6,50,000", "unit": "onwards"},
        {"name": "Kitchen renovation (same layout)", "price": "₹95,000", "unit": "onwards"},
    ],
    "ideal_customer": "Families in west Pune who just got possession of a 2 or 3BHK, and builders and architects who need sample flats or bulk kitchens",
    "why_us": "Delivered and installed in 45 days, or we pay you ₹1,000 a day\nOwn factory in Bhosari, so no middlemen\n3D design before you pay anything\n10-year warranty on hardware",
    "proof": "640+ kitchens installed in Pune since 2014",
    "whatsapp": "98765 43210",
    "email": "hello@deshmukhkitchens.example",
    "language": "English",
    "review_link": "",
    "payment_note": "UPI: deshmukhkitchens@okzz · 50% advance, 40% before installation, 10% on handover",
    "pan": "AAJCD4821K",
    "state_code": "27",
    "staff": 18,
    "terms_days": 15,
    "gst_rate": 18,
    "bank": "ZZ Bank Ltd, Baner branch",
    "account": "XXXXXXXX4417",
    "monthly_target": 2500000,
    "brand_message": "Your dream kitchen, installed in 45 days — or we pay you for every day late.",
}
BIZ["gstin"] = gstin(BIZ["state_code"], BIZ["pan"])

FIXED_COSTS = [  # (name, rupees a month, day, kind)
    ("Staff salaries (18 people)", 380000, 1, "salary"),
    ("Showroom rent, Baner", 65000, 5, "rent"),
    ("Factory rent, Bhosari", 48000, 5, "rent"),
    ("Delivery tempo EMI", 21400, 10, "emi"),
    ("Factory electricity (MSEDCL)", 19000, 18, "utility"),
    ("Accounting and design software", 6500, 3, "subscription"),
]

VENDORS = {  # invented suppliers, each with a checksum-valid GSTIN
    "plywood": ("Shree Balaji Plywood & Laminates", "Shop 12, Timber Market, Bhavani Peth, Pune 411042", gstin("27", "AAKFS7712R")),
    "hardware": ("Pune Hardware & Fittings Co.", "88, Narayan Peth, Laxmi Road, Pune 411030", gstin("27", "AAGPH3398L")),
    "granite": ("Sahyadri Granite & Quartz", "Survey 41, Pisoli Road, Kondhwa, Pune 411048", gstin("27", "ABCFS5521D")),
    "ads": ("ClickReach Digital Marketing LLP", "Office 702, Westport Tower, Baner, Pune 411045", gstin("27", "AAQFC6610M")),
}

# Customers: builders and architects (B2B) and families (retail). A few regulars have gone quiet on purpose.
#  (name, mobile, gstin or "", kind, purchases in 12 months, typical bill, last purchase days ago, pays after days)
CUSTOMERS = [
    ("Vastu Realty LLP", "9822011001", gstin("27", "AAKFV2210C"), "builder", 15, 420000, 5, 38),
    ("Shubham Builders & Developers", "9822011002", gstin("27", "AAHFS6651B"), "builder", 9, 310000, 14, 52),
    ("Ar. Meera Kulkarni Design Studio", "9822011003", gstin("27", "AQBPK4410F"), "architect", 10, 140000, 9, 21),
    ("Hotel Sahyadri Residency", "9822011004", gstin("27", "AADFH8832K"), "hotel", 5, 260000, 160, 30),   # gone quiet
    ("Gokhale Associates (Architects)", "9822011005", gstin("27", "AAEFG1170P"), "architect", 7, 95000, 120, 18),  # gone quiet
    ("Nirmiti Homes Pvt Ltd", "9822011006", gstin("27", "AAFCN9054H"), "builder", 6, 380000, 32, 75),
    ("The Spice Route Cloud Kitchen", "9822011007", gstin("27", "ABGFT3301Q"), "restaurant", 4, 185000, 100, 12),  # gone quiet
    ("Ar. Sanjay Bhide", "9822011008", "", "architect", 6, 120000, 25, 28),
]
FAMILIES = ["Neha Joshi", "Amit Patwardhan", "Priya Iyer", "Rohan Kale", "Sneha Gokhale", "Vikram Shinde", "Anjali Deshpande",
            "Kunal Mehta", "Swati Bapat", "Nikhil Rane", "Pooja Kulkarni", "Aditya Pawar", "Ritu Sharma", "Sameer Joglekar",
            "Kavita Naik", "Harsh Vora", "Mrunal Sathe", "Yash Agarwal"]


def sales_rows(seed: int = 41):
    """12 months of invoices: [date, customer, mobile, gstin, invoice_no, taxable, cgst, sgst, igst, total, due, paid_on]."""
    rng = random.Random(seed)
    rows = []
    for name, mob, gst, kind, n, amt, last, pay in CUSTOMERS:
        days = sorted(rng.sample(range(last + 3, 360), n - 1)) + [last]
        for ago in days:
            rows.append((TODAY - timedelta(days=ago), name, mob, gst, amt, pay))
    for i, fam in enumerate(FAMILIES):
        ago = rng.randint(4, 355)
        amt = rng.choice([165000, 185000, 210000, 95000, 120000, 650000, 240000])
        rows.append((TODAY - timedelta(days=ago), fam, f"98{rng.randint(10000000, 99999999)}", "", amt, rng.choice([0, 0, 2, 5, 9])))
    rows.sort(key=lambda r: r[0])
    out, no = [], 0
    for d, name, mob, gst, amt, pay in rows:
        no += 1
        taxable = round(amt * rng.uniform(.8, 1.2), -2)
        tax = round(taxable * .18, 2)
        due = d + timedelta(days=BIZ["terms_days"] if gst else 0)
        paid_day = d + timedelta(days=max(0, int(pay * rng.uniform(.6, 1.5))))
        paid = paid_day if paid_day <= TODAY - timedelta(days=2) else None
        fy = d.year if d.month >= 4 else d.year - 1
        inv = f"DMK/{str(fy)[2:]}-{str(fy + 1)[2:]}/{no:04d}"
        out.append([d, name, mob, gst, inv, taxable, round(tax / 2, 2), round(tax / 2, 2), 0.0, round(taxable + tax, 2), due, paid])
    return out


def bank_rows(seed: int = 7):
    """Jul–Sep 2026 current account, HDFC-style columns: [date, narration, ref, value_date, withdrawal, deposit, balance]."""
    rng = random.Random(seed)
    rows, bal, ref = [], 842610.55, 731200400

    def add(d, narr, wd=0.0, dep=0.0):
        nonlocal bal, ref
        ref += rng.randint(13, 89)
        bal = round(bal - wd + dep, 2)
        rows.append([d, narr, f"{ref:012d}", d, wd, dep, bal])

    def upi(n):
        return f"50{rng.randint(1000000000, 9999999999)}"

    for y, m in [(2026, 7), (2026, 8), (2026, 9)]:
        def d(day):
            return date(y, m, day)
        add(d(1), "SALARY TRF STAFF PAYROLL BATCH 18 EMP", wd=380000.00)
        add(d(2), f"NEFT CR-ZZBK0000118-VASTU REALTY LLP-INV DMK{rng.randint(300, 499)}", dep=round(rng.uniform(380000, 520000), 2))
        add(d(3), f"UPI/DR/{upi(1)}/DESIGNSOFT SAAS/ZZBK/zz.designsoft@okzz/Software", wd=6500.00)
        add(d(4), f"UPI/CR/{upi(1)}/AMIT PATWARDHAN/ZZBK/zz.customer21@okzz/Kitchen advance", dep=round(rng.uniform(70000, 95000), 2))
        add(d(5), f"UPI/DR/{upi(1)}/SAI SQUARE ESTATES/ZZBK/zz.saisquare@okzz/Showroom rent", wd=65000.00)
        add(d(5), f"NEFT DR-ZZBK0000207-MIDC PLOT OWNER C17-FACTORY RENT", wd=48000.00)
        add(d(6), f"NEFT DR-ZZBK0000310-SHREE BALAJI PLYWOOD LAMINATES-STOCK", wd=round(rng.uniform(310000, 420000), 2))
        add(d(8), f"UPI/DR/{upi(1)}/HPCL ZZ FUELS/ZZBK/zz.fuel@okzz/Tempo diesel", wd=round(rng.uniform(6200, 8900), 2))
        add(d(9), f"RTGS CR-ZZBK0000222-NIRMITI HOMES PVT LTD-SAMPLE FLATS", dep=round(rng.uniform(300000, 450000), 2))
        add(d(10), "ACH D- ZZ AUTO FINANCE LTD-TEMPO EMI 0000774411", wd=21400.00)
        add(d(11), f"NEFT DR-ZZBK0000415-PUNE HARDWARE FITTINGS CO-HINGES CHANNELS", wd=round(rng.uniform(85000, 130000), 2))
        add(d(12), "ATM WDL ZZBK ATM BANER PUNE 1234XXXXXXXX4417", wd=20000.00)
        add(d(13), f"UPI/CR/{upi(1)}/PRIYA IYER/ZZBK/zz.customer22@okzz/Wardrobe", dep=round(rng.uniform(45000, 90000), 2))
        add(d(14), f"NEFT DR-ZZBK0000512-SAHYADRI GRANITE QUARTZ-COUNTERTOPS", wd=round(rng.uniform(95000, 160000), 2))
        add(d(15), f"UPI/DR/{upi(1)}/CLICKREACH DIGITAL/ZZBK/zz.clickreach@okzz/Ads", wd=round(rng.uniform(25000, 38000), 2))
        add(d(16), f"NEFT CR-ZZBK0000603-AR MEERA KULKARNI DESIGN STUDIO-PAYMENT", dep=round(rng.uniform(110000, 170000), 2))
        add(d(17), "RTGS CR-ZZBK0000118-VASTU REALTY LLP-WAKAD MILESTONE PAYMENT", dep=round(rng.uniform(420000, 560000), 2))
        add(d(18), "BILLPAY MSEDCL CONSUMER 170044556677 ELECTRICITY", wd=round(rng.uniform(17500, 21500), 2))
        add(d(19), f"UPI/DR/{upi(1)}/AIRTEL/ZZBK/zz.airtel@okzz/Broadband", wd=1999.00)
        add(d(20), "GST PAYMENT CHALLAN CPIN 2627000123456 GSTR3B", wd=round(rng.uniform(320000, 390000), 2))
        add(d(21), f"IMPS/P2A/{upi(1)}/ROHAN KALE/Kitchen balance", dep=round(rng.uniform(60000, 110000), 2))
        add(d(22), f"UPI/DR/{upi(1)}/RAMESH TEMPO SERVICES/ZZBK/zz.ramesh@okzz/Transport", wd=round(rng.uniform(9000, 15000), 2))
        add(d(24), f"NEFT CR-ZZBK0000118-SHUBHAM BUILDERS DEVELOPERS-INV DMK{rng.randint(300, 499)}", dep=round(rng.uniform(250000, 380000), 2))
        add(d(23), f"NEFT CR-ZZBK0000721-GOKHALE ASSOCIATES-INV DMK{rng.randint(300, 499)}" if m < 9 else f"UPI/CR/{upi(1)}/KUNAL MEHTA/ZZBK/zz.customer23@okzz/2BHK advance",
            dep=round(rng.uniform(110000, 190000), 2))
        add(d(25), "TRF TO OWN AC XXXXXXXX9902 DESHMUKH SAVINGS", wd=100000.00)
        add(d(26), f"UPI/DR/{upi(1)}/SWIGGY/ZZBK/zz.swiggy@okzz/Site team lunch", wd=round(rng.uniform(2400, 4200), 2))
        add(d(27), f"UPI/DR/{upi(1)}/LABOUR CONTRACTOR SANTOSH/ZZBK/zz.santosh@okzz/Installation labour", wd=round(rng.uniform(55000, 80000), 2))
        add(d(28), "CHQ PAID 000318 PUNE MUNICIPAL CORPORATION SHOP LICENCE" if m == 7 else f"UPI/DR/{upi(1)}/ZZ INSURANCE/ZZBK/zz.ins@okzz/Shop insurance",
            wd=4500.00 if m == 7 else 3150.00)
        add(d(28), "INTEREST CREDIT" if m == 9 else "SMS CHARGES QTR" if m == 8 else "DEBIT CARD ANNUAL FEE", dep=1840.25 if m == 9 else 0.0,
            wd=0.0 if m == 9 else (17.70 if m == 8 else 590.00))
    rows.sort(key=lambda r: r[0])        # in date order, with the running balance worked out again
    bal = 842610.55
    for r in rows:
        bal = round(bal - r[4] + r[5], 2)
        r[6] = bal
    return rows


# Things to type or paste in the hub
FUNNEL = {"month": "September 2026", "visitors": 2140, "inquiries": 64, "meetings": 22, "proposals": 17, "won": 6, "avg_sale": 210000}

INQUIRIES = [  # (channel, name, what to type, what it shows)
    ("Website form", "Neha Joshi", "Hi, we got possession of a 3BHK in Balewadi last week. Need an L-shaped kitchen and two wardrobes. What will it cost and how long does it take?",
     "Follow-ups (day 1, 3, 7); Lead Response first reply; a quotation"),
    ("Website form", "Amit Patwardhan", "Kitchen renovation for a 15-year-old flat in Kothrud. Same layout, want new shutters, quartz top and a chimney.",
     "Follow-up 1 of 3 the next working day"),
    ("WhatsApp (paste into Inquiries)", "Vastu Realty LLP – Mr. Sathe", "Need quotation for 24 modular kitchens for our Wakad project, possession March. Please share rates for 10 ft L-shape with granite.",
     "A big B2B inquiry: mark it won later and the follow-ups stop"),
    ("Website form", "Priya Iyer", "Do you do only wardrobes? 2 sliding wardrobes, 7 ft x 8 ft each, for a flat in Aundh.",
     "A price only if it's in your Knowledge Hub (₹1,450 per sq ft)"),
    ("Lead magnet / guide", "Kunal Mehta", "Downloaded your kitchen planning guide. Want a site visit this Saturday for a 2BHK in Pimple Saudagar.",
     "Lead Response offers two times inside your hours"),
    ("Email (if inquiry email is set up)", "Ar. Sanjay Bhide", "Sending a client your way: 4BHK in Koregaon Park, full interiors, budget about 25 lakh. Can you meet her next week?",
     "An email inquiry gets a proposal by email, not WhatsApp"),
]

REVIEWS = [  # (source, stars, reviewer, date offset, text, what it shows)
    ("Google", 2, "Sanjana P.", 3, "Kitchen looks good but installation was 9 days late and the site was left dusty. Had to call 4 times for the final cleaning.",
     "A calm, specific reply with no prices or discounts; approve, copy, I've posted it"),
    ("Justdial", 1, "Rajesh K.", 6, "Gave advance in July, still waiting for the wardrobe shutters. Nobody picks up the phone after 6 pm.",
     "An unhappy customer: the draft apologises and offers a call, never invents a date"),
    ("Facebook", 3, "Megha Rao", 10, "Good quality material. Design team was helpful but final bill was a little more than the first quotation because of extra drawers.",
     "A mixed review: thanks them and explains, without arguing"),
    ("Google", 5, "Anand Gokhale", 2, "Third kitchen we have done with Deshmukh (two for our rental flats). Delivered in 41 days, the 3D design matched exactly. Highly recommended.",
     "Ask for a referral appears; WhatsApp opens with the message typed"),
    ("Google", 5, "Pooja & Nikhil", 12, "Our 2BHK interiors were done before Diwali as promised. Rahul and team were patient with all our changes. The pull-out pantry is our favourite!",
     "Ask for a referral; a good case study"),
    ("IndiaMART", 4, "Hotel Sahyadri Residency", 20, "Commercial kitchen storage done well. Minor delay in delivery of SS shelves but the team fixed it quickly.",
     "A B2B review: the page suggests referrals and case studies"),
]

COMPETITORS = [  # real, public websites of national modular-kitchen brands, used only as competitors to watch
    ("Livspace", "https://www.livspace.com", "Livspace", "National brand, strong in full-home interiors; watch their offers before festivals"),
    ("HomeLane", "https://www.homelane.com", "HomeLane", "National brand; watch kitchen prices and EMI offers"),
    ("Design Cafe", "https://www.designcafe.com", "DesignCafe", "Experience centres in big cities; watch new collections"),
]

RESEARCH = [
    ("Real estate developers with projects near possession", "Pune", "Bulk kitchens and sample flats"),
    ("Architects and interior designers who don't make their own furniture", "Pune", "Factory partner for their clients"),
    ("Restaurants and cloud kitchens", "Pimpri-Chinchwad", "Commercial kitchen storage"),
]

WINBACK = {"days": 90, "min_purchases": 2, "per_week": 20,
           "offer": "Free hinge and channel servicing for past customers this November — just reply to book a slot."}

LEAD_RESPONSE = {"booking": "Offer two times", "what": "a site visit", "hours": "10-19", "prices_to_person": True,
                 "template": "lead_response_v1",
                 "body": "Hello {{1}}, thank you for contacting {{2}}. {{3}} We look forward to speaking with you."}

EXPENSES = [  # (file, date offset, vendor key or name, category hint, taxable, gst, claimable, paid by, note)
    ("bill_01_plywood_laminates.pdf", 18, "plywood", "Raw material", 184500.00, 33210.00, True, "Bank transfer", "BWP plywood and laminates for Vastu Realty order"),
    ("bill_02_hardware_fittings.pdf", 15, "hardware", "Hardware & fittings", 62800.00, 11304.00, True, "Bank transfer", "Soft-close hinges, tandem boxes, channels"),
    ("bill_03_quartz_countertops.pdf", 11, "granite", "Raw material", 96400.00, 17352.00, True, "Bank transfer", "Quartz tops for 6 kitchens"),
    ("bill_04_digital_ads.pdf", 8, "ads", "Marketing & ads", 30000.00, 5400.00, True, "UPI", "October search and Instagram ads"),
    ("bill_05_msedcl_electricity.pdf", 5, "MSEDCL (Factory)", "Electricity & power", 18640.00, 0.0, False, "Bank transfer", "Factory electricity, September reading"),
    ("bill_06_tempo_transport_receipt.jpg", 2, "Ramesh Tempo Services", "Freight & courier", 12500.00, 0.0, False, "Cash", "3 deliveries to Wakad and Hinjewadi sites"),
]
