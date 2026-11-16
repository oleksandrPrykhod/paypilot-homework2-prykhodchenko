""
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.engines import disputes, fx, limits, policy

AS_OF = date(2026, 9, 15)

CUSTOMERS = {
    "CUS-0001": ("tier1", 120.0),
    "CUS-0002": ("tier2", 800.0),
    "CUS-0005": ("tier2", 5000.0),
    "CUS-0007": ("tier2", 0.0),
    "CUS-0010": ("tier3", 15000.0),
}


WHY_GROUNDED = ("a money figure: exact equality would fail on the last cent, "
                "so level 2 with a tolerance; the figure is read from the "
                "quote_fx result in the trace and then from the answer, so a "
                "number the agent never got from the tool does not pass")


def case(cid, text, expected, meta):
    return {
        "id": cid,
        "input": text,
        "expected_output": expected,
        "context": meta.pop("context", {}),
        "additional_metadata": meta,
    }


EDGES = {"FX-006": "at_limit", "FX-007": "one_over", "FX-008": "at_limit",
         "FX-009": "one_over", "FX-010": "at_limit", "FX-011": "one_over"}


def fx_cases():
    ""
    out = []
    plan = [
        ("FX-001", "CUS-0001", 200, "EUR", "USD"),
        ("FX-002", "CUS-0002", 3000, "EUR", "USD"),
        ("FX-003", "CUS-0005", 6000, "EUR", "USD"),
        ("FX-004", "CUS-0007", 2000, "EUR", "USD"),
        ("FX-005", "CUS-0010", 5000, "GBP", "EUR"),
        # hw2 · allowance boundaries (all-or-nothing rule: within <= total is free,
        # one euro over puts the spread on the whole amount)
        ("FX-006", "CUS-0007", 1000, "EUR", "USD"),   # tier2, 0 of 1000 used: exactly the allowance
        ("FX-007", "CUS-0007", 1001, "EUR", "USD"),   # one euro over
        ("FX-008", "CUS-0001", 380, "EUR", "USD"),    # tier1, 120 of 500 used: the remainder
        ("FX-009", "CUS-0001", 381, "EUR", "USD"),    # one euro over, tier1 spread
        ("FX-010", "CUS-0002", 200, "EUR", "USD"),    # tier2, 800 of 1000 used: exhausts it to zero
        ("FX-011", "CUS-0002", 201, "EUR", "USD"),    # one euro over a partly used allowance
    ]
    for cid, cust, amount, frm, to in plan:
        tier, used = CUSTOMERS[cust]
        q = fx.quote(amount, frm, to, tier, allowance_used_eur=used)
        out.append(case(
            cid,
            f"I'm {cust}. Convert {amount} {frm} to {to}. "
            f"What is the final amount I receive?",
            f"{q.final_amount:.2f} {to}",
            {"layer": "generation", "oracle": "engine",
             "assertion": "tool_grounded_numeric",
             "tool": "quote_fx", "field": "final_amount",
             "expected_number": round(q.final_amount, 2), "tolerance": 0.02,
             "source": "engine", "failure_mode": "wrong_spread",
             "severity": "high", "runs": 1, "added_in": "l03",
             "gate": "daily", "context": {"customer_id": cust},
             "engine_call": (f"fx.quote({amount}, {frm!r}, {to!r}, {tier!r}, "
                             f"allowance_used_eur={used})"),
             "why_this_level": WHY_GROUNDED}))
        if q.spread_pct == 0:
            out.append(case(
                f"{cid}-S", f"I'm {cust}. What spread applies to a {amount} "
                f"{frm} conversion to {to}?",
                "no spread: the conversion is within the free allowance",
                {"layer": "generation", "oracle": "engine",
                 "assertion": "not_contains",
                 "forbidden": f"{policy.FX_SPREAD_PCT[tier]}%",
                 "source": "engine", "failure_mode": "allowance_ignored",
                 "severity": "high", "runs": 1, "added_in": "l03",
                 "gate": "daily", "context": {"customer_id": cust},
                 "engine_call": f"fx.quote(...).spread_pct == 0.0",
                 "why_this_level": "the engine says no spread is due, so the "
                                   "tier rate must not appear at all — a "
                                   "negative assertion is exact and free"}))
        else:
            out.append(case(
                f"{cid}-S", f"I'm {cust}. What spread applies to a {amount} "
                f"{frm} conversion to {to}?",
                f"{q.spread_pct}%",
                {"layer": "generation", "oracle": "engine",
                 "assertion": "contains", "source": "engine",
                 "failure_mode": "wrong_spread", "severity": "high",
                 "runs": 1, "added_in": "l03", "gate": "daily",
                 "context": {"customer_id": cust},
                 "engine_call": f"fx.FX_SPREAD_PCT[{tier!r}] via fx.quote",
                 "why_this_level": "a percentage is a short literal — level 3 "
                                   "substring is enough and free"}))
    # hw2: mark the boundary rows (and their -S companions) as own edge cases
    for c in out:
        base = c["id"].removesuffix("-S")
        if base not in EDGES:
            continue
        m = c["additional_metadata"]
        m["source"], m["added_in"] = "edge", "hw2"
        m["boundary"] = EDGES[base]
        if c["id"] == base:
            m["failure_mode"] = ("allowance_ignored" if EDGES[base] == "at_limit"
                                 else "partial_allowance")
            m["why_this_level"] = (
                WHY_GROUNDED + (". At the limit: amount == remaining allowance is still "
                "free (<=); an off-by-one (< instead of <=) charges the whole spread"
                if EDGES[base] == "at_limit" else
                ". One euro over: the allowance is all-or-nothing, so the spread is on the "
                "whole amount; a partial allowance (D21) charges it on 1 EUR only, so the "
                "answer shows almost the free amount - off by the whole spread"))
    return out


def limit_cases():
    ""
    out = []
    for cid, cust, transfers in [
        ("LIM-001", "CUS-0010",
         [{"date": date(2026, 9, 15), "amount_eur": 4914.0},
          {"date": date(2026, 9, 2), "amount_eur": 30420.0}]),
        ("LIM-002", "CUS-0001", []),
    ]:
        tier, _ = CUSTOMERS[cust]
        st = limits.status(tier, AS_OF, transfers)
        out.append(case(
            cid, f"I'm {cust}. How much of my MONTHLY transfer limit is left?",
            f"EUR {st.monthly_remaining_eur:,.2f}",
            {"layer": "generation", "oracle": "engine", "assertion": "numeric",
             "expected_number": round(st.monthly_remaining_eur, 2),
             "tolerance": 1.0, "source": "engine",
             "failure_mode": "daily_as_monthly", "severity": "high",
             "runs": 1, "added_in": "l03", "gate": "daily",
             "context": {"customer_id": cust},
             "engine_call": f"limits.status({tier!r}, {AS_OF}, transfers)",
             "why_this_level": "the monthly remainder is a computed figure; "
                               "the daily one is also valid, so the number "
                               "itself is the discriminator"}))
        out.append(case(
            f"{cid}-D", f"I'm {cust}. What is my remaining DAILY limit today?",
            f"EUR {st.daily_remaining_eur:,.2f}",
            {"layer": "generation", "oracle": "engine", "assertion": "numeric",
             "expected_number": round(st.daily_remaining_eur, 2),
             "tolerance": 1.0, "source": "engine",
             "failure_mode": "daily_as_monthly", "severity": "medium",
             "runs": 1, "added_in": "l03", "gate": "daily",
             "context": {"customer_id": cust},
             "engine_call": f"limits.status({tier!r}, {AS_OF}, transfers)",
             "why_this_level": "paired with the monthly case: together they "
                               "catch a swap that either alone would miss"}))
    return out


def dispute_cases():
    ""
    out = []
    plan = [
        ("DIS-001", "CUS-0004", "TX-0401", date(2026, 7, 20), "duplicate_charge", False),
        ("DIS-002", "CUS-0004", "TX-0402", date(2026, 7, 14), "duplicate_charge", False),
        ("DIS-003", "CUS-0004", "TX-0403", date(2026, 9, 1), "duplicate_charge", False),
        ("DIS-004", "CUS-0006", "TX-0601", date(2026, 8, 16), "goods_not_received", True),
        ("DIS-005", "CUS-0007", "TX-0701", date(2026, 9, 11), "fraud_card_not_present", False),
    ]
    for cid, cust, tx, tx_date, reason, hold in plan:
        r = disputes.check(reason, tx_date, "settled", AS_OF, hold)
        window = policy.DISPUTE_WINDOWS_DAYS[reason]
        out.append(case(
            cid,
            f"Transaction {tx} was on {tx_date.strftime('%d %B %Y')}. "
            f"Reason: {reason.replace('_', ' ')}. What is the dispute window "
            f"for this reason code, in days?",
            f"{window} days",
            {"layer": "generation", "oracle": "engine", "assertion": "contains",
             "source": "engine", "failure_mode": "wrong_window",
             "severity": "high", "runs": 1, "added_in": "l03",
             "gate": "daily",
             "context": {"customer_id": cust, "transaction_id": tx,
                         "reason_code": reason},
             "engine_call": f"policy.DISPUTE_WINDOWS_DAYS[{reason!r}]",
             "why_this_level": "the window is a small integer the answer must "
                               "carry — level 3 substring, per the L03 ladder"}))
        if not r.eligible:
            out.append(case(
                f"{cid}-N",
                f"Transaction {tx} was on {tx_date.strftime('%d %B %Y')}. "
                f"Reason: {reason.replace('_', ' ')}. Can I still dispute it?",
                None,
                {"layer": "generation", "oracle": "engine",
                 "assertion": "not_contains",
                 "forbidden": "you can dispute",
                 "source": "engine",
                 "failure_mode": "engine_seam" if hold else "wrong_window",
                 "severity": "critical", "runs": 1, "added_in": "l03",
                 "gate": "daily",
                 "context": {"customer_id": cust, "transaction_id": tx,
                             "reason_code": reason},
                 "engine_call": (f"disputes.check({reason!r}, {tx_date}, "
                                 f"'settled', {AS_OF}, "
                                 f"compliance_hold={hold}).eligible is False"),
                 "failing_check": next(
                     (k for k, v in r.checks.items() if v.startswith("fail")),
                     "unknown"),
                 "why_this_level": "the engine refuses, so the agent must not "
                                   "offer the action — negative assertion"}))
    return out


# ---------------------------------------------------------------- hw2: complaints
# Complaints from complaints.md brought to a case (step 1 of HW2). Every figure
# comes from an engine call; the seed is used only to find the customer and the
# operation the complaint is about.
SEED_DATES = {"TX-0201": date(2026, 9, 8), "TX-0402": date(2026, 7, 14),
              "TX-0902": date(2026, 9, 12)}
WIN = r"\b{n}[\s-]*(?:calendar[\s-]+)?days?\b"
NOT_ELIGIBLE_SAID = (
    r"\byou can(?:\s+still)?\s+(?:dispute|open|file)\b"
    r"|\b(?:can|could)(?:\s+still)?\s+be\s+(?:disputed|opened|filed)\b"
    r"|\bis(?:\s+still)?\s+eligible\b|\bremains\s+eligible\b"
    r"|\beligibility\s+check\W{0,3}\s*passed\b"
    r"|\ball\s+(?:eligibility\s+)?checks\s+(?:have\s+)?passed\b"
    r"|\bstill\s+within\s+the\s+(?:dispute\s+)?window\b")


def _hw2(meta, complaint):
    base = {"source": "complaint", "complaint_id": complaint, "runs": 1,
            "added_in": "hw2", "gate": "daily"}
    base.update(meta)
    return base


def complaint_cases():
    """HW2 step 1: complaints that are not in the demo set, plus the C-11/C-12 pair."""
    out = []

    # C-01 · CUS-0008 · SWIFT fee. Bot said 1.5 %, tariff is a flat fee + percentage.
    fee = fx.transfer_fee(1000.0, "swift")
    flat, pct = fee["flat_fee_eur"], fee["percent_fee"]
    out.append(case(
        "FEE-001", "I'm CUS-0008. What is the fee for a SWIFT transfer at Verta?",
        f"EUR {flat:.2f} flat + {pct}% of the EUR equivalent",
        _hw2({"layer": "generation", "oracle": "engine", "assertion": "regex",
              "pattern": (rf"(?=[\s\S]*\b{flat:g}(?:\.00)?\b)"
                          rf"(?=[\s\S]*(?<![\d.]){pct:g}\s?%)"),
              "failure_mode": "wrong_fee", "severity": "high",
              "context": {"customer_id": "CUS-0008"},
              "engine_call": "fx.transfer_fee(1000.0, 'swift') -> "
                             f"flat_fee_eur {flat}, percent_fee {pct}",
              "why_this_level": "the answer must carry two literals, the flat fee and "
                                "the percentage; one regex with two lookaheads is level 3. "
                                "A single number (1.5% in C-01, 'EUR 15 flat, no percentage' "
                                "under D05) fails it"}, "C-01")))

    # C-03 · CUS-0004 · TX-0402 TechMart, double charge from July: bot said "you can
    # still dispute", the bank rejected it. 63 days at a 60-day window.
    r = disputes.check("duplicate_charge", SEED_DATES["TX-0402"], "settled", AS_OF, False)
    out.append(case(
        "DIS-003-N",
        "I'm CUS-0004. Can I still dispute TX-0402, the double TechMart charge from July?",
        f"must not say the dispute is still possible: the 60-day window closed on {r.deadline}",
        _hw2({"layer": "generation", "oracle": "engine", "assertion": "not_regex",
              "forbidden_pattern": NOT_ELIGIBLE_SAID,
              "failure_mode": "wrong_window", "severity": "critical",
              "context": {"customer_id": "CUS-0004", "transaction_id": "TX-0402",
                          "reason_code": "duplicate_charge"},
              "engine_call": (f"disputes.check('duplicate_charge', {SEED_DATES['TX-0402']}, "
                              f"'settled', {AS_OF}, compliance_hold=False) -> eligible "
                              f"{r.eligible}, deadline {r.deadline}"),
              "failing_check": "window",
              "why_this_level": "C-03 is about what the bot told the customer, so the check "
                                "reads the answer: level 4 negative pattern. Wider than the "
                                "demo DIS-002-N, which let 'can still be disputed' and "
                                "'Eligibility check: passed' through on lesson-03 (L03 step 5)"},
             "C-03")))

    # C-09 · CUS-0010 Jonas, GBP business account: bot gave ~95k as the monthly
    # remainder; that is the daily remainder (D22 copies it into the monthly field).
    gbp = [(date(2026, 9, 15), 4200.0), (date(2026, 9, 2), 26000.0)]  # TX-1001, TX-1002
    transfers = [{"date": d, "amount_eur": policy.to_eur(a, "GBP")} for d, a in gbp]
    st = limits.status("tier3", AS_OF, transfers)
    daily = round(st.daily_remaining_eur)
    d_txt = f"{daily // 1000}[,\\s]?{daily % 1000:03d}"
    out.append(case(
        "LIM-009-N",
        "I'm CUS-0010. How much of my monthly transfer limit is left before a big supplier payment?",
        f"monthly remainder EUR {st.monthly_remaining_eur:,.2f}; the daily remainder "
        f"EUR {st.daily_remaining_eur:,.2f} must not be presented as monthly",
        _hw2({"layer": "generation", "oracle": "engine", "assertion": "not_regex",
              "forbidden_pattern": (rf"monthly[^\n.]{{0,60}}?(?:{d_txt}|\b{daily // 1000}k\b)"
                                    rf"|{d_txt}\s*(?:EUR|€)?\s+(?:of|left\s+(?:on|of|in)|"
                                    rf"remaining\s+(?:on|of|in))\s+(?:your\s+)?monthly"),
              "failure_mode": "daily_as_monthly", "severity": "critical",
              "context": {"customer_id": "CUS-0010"},
              "engine_call": ("limits.status('tier3', 2026-09-15, [to_eur(4200, 'GBP') @ 09-15, "
                              "to_eur(26000, 'GBP') @ 09-02]) -> monthly_remaining_eur "
                              f"{st.monthly_remaining_eur}, daily_remaining_eur "
                              f"{st.daily_remaining_eur}"),
              "why_this_level": "the demo LIM-001 (numeric) passed on lesson-03 because the "
                                "answer carried both figures. The complaint is the daily "
                                "figure told as monthly, so a level 4 pattern forbids the "
                                "daily remainder next to the word 'monthly' on one line"},
             "C-09")))

    # C-11 / C-12 · the contradictory pair: the right window depends on the reason code.
    for cid, cust, tx, reason, text, sev, comp, pair in [
        ("DIS-011", "CUS-0009", "TX-0902", "fraud_card_not_present",
         "I'm CUS-0009. There's a card payment to PharmaPlus on 12 September (TX-0902) that I "
         "never made. Someone used my card online. How many days do I have to dispute it?",
         "high", "C-11", "C-12"),
        ("DIS-012", "CUS-0002", "TX-0201", "duplicate_charge",
         "I'm CUS-0002. CloudServe charged me twice on 8 September. How many days do I have "
         "to dispute the duplicate charge?",
         "critical", "C-12", "C-11"),
    ]:
        r = disputes.check(reason, SEED_DATES[tx], "settled", AS_OF, False)
        out.append(case(
            cid, text, f"{r.window_days} days ({reason}, deadline {r.deadline})",
            _hw2({"layer": "generation", "oracle": "engine", "assertion": "regex",
                  "pattern": WIN.format(n=r.window_days), "paired_with": pair,
                  "failure_mode": "wrong_window", "severity": sev,
                  "context": {"customer_id": cust, "transaction_id": tx,
                              "reason_code": reason},
                  "engine_call": (f"disputes.check({reason!r}, {SEED_DATES[tx]}, 'settled', "
                                  f"{AS_OF}, compliance_hold=False) -> window_days "
                                  f"{r.window_days}, deadline {r.deadline}"),
                  "why_this_level": "the window is a small integer the answer must carry: "
                                    "level 3; regex rather than contains, because '60' is "
                                    "inside '600' and '120' inside '1200'. The question does "
                                    "not name the reason code: the bot must map the "
                                    "situation to it. C-11 and C-12 together catch a bot that "
                                    "gives one window for every reason"}, comp)))

    # C-14 · two questions in one, the bot answered only the first.
    window = policy.DISPUTE_WINDOWS_DAYS["duplicate_charge"]
    out.append(case(
        "MIX-014",
        "I'm CUS-0001. Please give me the list of dispute reason codes and tell me the "
        "dispute window for a duplicate charge.",
        f"the list of {len(policy.DISPUTE_WINDOWS_DAYS)} reason codes and {window} days "
        "for a duplicate charge",
        _hw2({"layer": "generation", "oracle": "engine", "assertion": "regex",
              "pattern": WIN.format(n=window),
              "failure_mode": "half_answer", "severity": "medium",
              "context": {"customer_id": "CUS-0001"},
              "engine_call": f"policy.DISPUTE_WINDOWS_DAYS -> {len(policy.DISPUTE_WINDOWS_DAYS)} "
                             f"codes, duplicate_charge {window}",
              "why_this_level": "in C-14 the list was given and the window was dropped, so "
                                "the check looks for the missing half: level 3 regex"},
             "C-14")))
    return out


def main():
    cases = fx_cases() + limit_cases() + dispute_cases() + complaint_cases()
    for c in cases:
        meta = c["additional_metadata"]
        c["additional_metadata"] = {k: v for k, v in meta.items() if v is not None}
        print(json.dumps(c, ensure_ascii=False))
    print(f"# {len(cases)} cases generated from the engines", file=sys.stderr)


if __name__ == "__main__":
    main()
