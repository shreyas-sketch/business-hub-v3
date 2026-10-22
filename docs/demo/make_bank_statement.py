"""Writes docs/demo/kaveri-bank-statement.csv: an invented 3-month current-account statement (HDFC-style columns) for the
launch demo business, Kaveri Home Appliances. Every name, number, account and handle is invented."""
import random
from datetime import date, timedelta

rng = random.Random(22)
rows = []
bal = 512850.40       # high enough that the account never goes below zero
months = [(2026, 7), (2026, 8), (2026, 9)]
ref = 600100200


def add(d, narr, wd=0.0, dep=0.0):
    global bal, ref
    ref += rng.randint(11, 97)
    bal = round(bal - wd + dep, 2)
    rows.append((d.strftime("%d/%m/%y"), narr, f"{ref:012d}", d.strftime("%d/%m/%y"), f"{wd:.2f}" if wd else "", f"{dep:.2f}" if dep else "", f"{bal:.2f}"))


customers = ["SAI DEVELOPERS", "PATIL RESIDENCY LLP", "HOTEL PANCHAVATI", "GOKHALE CLINIC", "JOSHI CATERERS", "ANAND KITCHEN STUDIO"]
for y, m in months:
    def d(day):
        return date(y, m, day)
    add(d(1), f"NEFT CR-ZZBK0000101-SAI DEVELOPERS-INV KHA{rng.randint(700, 999)}", dep=round(rng.uniform(150000, 240000), 2))
    add(d(1), "SALARY TRF STAFF PAYROLL BATCH 24 EMP", wd=420000.00)
    add(d(2), f"UPI/CR/50{rng.randint(1000000000, 9999999999)}/MEERA APTE/ZZBK/zz.customer11@okzz/Chimney", dep=round(rng.uniform(14000, 19000), 2))
    add(d(3), f"IMPS/P2A/50{rng.randint(1000000000, 9999999999)}/ROHAN KULKARNI/Purifier", dep=round(rng.uniform(11000, 16000), 2))
    add(d(4), f"NEFT CR-ZZBK0000111-{rng.choice(customers[1:])}-INV KHA{rng.randint(700, 999)}", dep=round(rng.uniform(140000, 260000), 2))
    add(d(5), "UPI/DR/50%d/SHREE ESTATES/ZZBK/zz.estates01@okzz/Showroom rent" % rng.randint(1000000000, 9999999999), wd=85000.00)
    add(d(6), "NEFT DR-ZZBK0000202-WHIRLWIND APPLIANCES DISTRIBUTORS-STOCK", wd=round(rng.uniform(380000, 520000), 2))
    add(d(7), "BILLPAY MSEDCL CONSUMER 170012345678 ELECTRICITY", wd=round(rng.uniform(16500, 21500), 2))
    add(d(8), "UPI/DR/50%d/HPCL ZZ FUELS/ZZBK/zz.fuel@okzz/Delivery van" % rng.randint(1000000000, 9999999999), wd=round(rng.uniform(5500, 8200), 2))
    add(d(9), f"NEFT CR-ZZBK0000303-{rng.choice(customers[1:4])}-PAYMENT", dep=round(rng.uniform(40000, 125000), 2))
    add(d(10), "ACH D- ZZ AUTO FINANCE LTD-DELIVERY VAN EMI 0000881234", wd=18500.00)
    add(d(11), "UPI/DR/50%d/AIRTEL/ZZBK/zz.airtel@okzz/Broadband" % rng.randint(1000000000, 9999999999), wd=2359.00)
    add(d(12), "ATM WDL ZZBK ATM BANER PUNE 1234XXXXXXXX5678", wd=10000.00)
    add(d(13), f"RTGS CR-ZZBK0000222-SAI DEVELOPERS-BULK ORDER", dep=round(rng.uniform(220000, 320000), 2))
    add(d(14), "NEFT DR-ZZBK0000404-PUNE ELECTRICALS TRADERS-FITTINGS", wd=round(rng.uniform(42000, 68000), 2))
    add(d(15), "UPI/DR/50%d/GOOGLE ADS/ZZBK/zz.ads@okzz/Ads" % rng.randint(1000000000, 9999999999), wd=round(rng.uniform(15000, 22000), 2))
    add(d(16), f"UPI/CR/50{rng.randint(1000000000, 9999999999)}/SNEHA PATIL/ZZBK/zz.customer12@okzz/Hob", dep=round(rng.uniform(21000, 33000), 2))
    add(d(18), "NEFT DR-ZZBK0000505-BLUEZZ COURIER SERVICES-FREIGHT", wd=round(rng.uniform(6000, 9500), 2))
    add(d(19), f"NEFT CR-ZZBK0000333-{rng.choice(customers[1:])}-INV", dep=round(rng.uniform(120000, 240000), 2))
    add(d(20), "GST CHALLAN CPIN 26092700001234 CBIC", wd=round(rng.uniform(52000, 78000), 2))
    add(d(21), f"NEFT CR-ZZBK0000606-{rng.choice(customers)}-INV", dep=round(rng.uniform(60000, 180000), 2))
    add(d(23), "UPI/DR/50%d/SWIGGY/ZZBK/zz.swiggy@okzz/Team lunch" % rng.randint(1000000000, 9999999999), wd=round(rng.uniform(2400, 4200), 2))
    add(d(24), "NEFT DR-ZZBK0000707-ZZ PACKAGING WORKS-BOXES", wd=round(rng.uniform(8000, 14000), 2))
    add(d(25), f"NEFT CR-ZZBK0000444-{rng.choice(customers)}-PAYMENT", dep=round(rng.uniform(110000, 230000), 2))
    add(d(26), f"UPI/CR/50{rng.randint(1000000000, 9999999999)}/AMIT JOSHI/ZZBK/zz.customer13@okzz/Chimney", dep=round(rng.uniform(13000, 18500), 2))
    add(d(27), "CHRG: NEFT/RTGS CHARGES INCL GST", wd=354.00)
    if m == 9:
        add(d(28), "NEFT DR-ZZBK0000808-ZZ AND ASSOCIATES CA-AUDIT FEES", wd=29500.00)
    add(d(28), "TRF TO OWN A/C XXXXXXXX4411 SWEEP FD", wd=100000.00)
    add(d(29), f"NEFT CR-ZZBK0000909-{rng.choice(customers[:3])}-PART PAYMENT", dep=round(rng.uniform(90000, 210000), 2))

head = ["ZZ BANK LTD - DEMO STATEMENT (INVENTED DATA)", "Account Name : KAVERI HOME APPLIANCES PVT LTD",
        "Account No : XXXXXXXX7731", "Statement From : 01/07/2026 To : 30/09/2026", ""]
lines = head + ["Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance"]
for r in rows:
    lines.append(",".join(f'"{x}"' if "," in x else x for x in r))
lines += ["", "*** End of statement — every name and number above is invented for the demo ***"]
open(__import__("pathlib").Path(__file__).with_name("kaveri-bank-statement.csv"), "w").write("\n".join(lines) + "\n")
print(len(rows), "rows; closing", bal)
