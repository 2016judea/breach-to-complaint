"""Notices -> incidents (DATA_MODEL.md §2-§5) and the triage score (§7)."""
from __future__ import annotations

import datetime as dt
import math
import re

# ------------------------------------------------------------- data elements
# (element, regex over the notice text). WA's structured information types map
# through WA_TYPES. Order is display order.
ELEMENTS = [
    ("ssn", r"social security|\bSSNs?\b"),
    ("drivers_license", r"driver'?s? licen[cs]e|state (?:issued )?id(?:entification)?(?: card)? number|washington id card"),
    ("passport", r"passport"),
    ("tax_id", r"taxpayer identification|\bTINs?\b|tax id"),
    ("financial_account", r"(?:bank|financial) account|account number|routing number|financial (?:& banking )?information"),
    ("payment_card", r"(?:credit|debit|payment) card"),
    ("medical", r"medical (?:information|record|history|condition|treatment)|diagnos[ie]s|treatment information|clinical|prescription|protected health information|\bPHI\b|health information"),
    ("health_insurance", r"health insurance|insurance (?:information|policy|member)|medicare|medicaid|member id"),
    ("biometric", r"biometric|fingerprint|face ?print|retina"),
    ("credentials", r"user ?names?\b.{0,40}passwords?|passwords?|login credentials|security (?:question|code)|session credentials"),
    ("dob", r"date of birth|birth ?date|\bDOB\b"),
    ("name", r"\bnames?\b"),
    ("email", r"e-?mail address"),
    ("address", r"(?:postal|mailing|home|street) address|\baddress(?:es)?\b"),
    ("phone", r"phone number|telephone"),
]
WA_TYPES = {
    "Social Security Number": "ssn",
    "Driver's License or Washington ID Card Number": "drivers_license",
    "Financial & Banking Information": "financial_account",
    "Full Date of Birth": "dob",
    "Name": "name",
    "Medical Information": "medical",
    "Health Insurance Policy or ID Number": "health_insurance",
    "Passport Number": "passport",
    "Username and Password/Security Question Answers": "credentials",
    "Biometric Data": "biometric",
    "Student, Military, or Passport ID": "passport",
    "Email Address and Password/Security Question Answers": "credentials",
}
SENSITIVE = {"ssn": 30, "medical": 25, "financial_account": 22, "payment_card": 20, "drivers_license": 20,
             "passport": 20, "tax_id": 20, "health_insurance": 18, "biometric": 25, "credentials": 12,
             "dob": 6, "address": 2, "phone": 2, "email": 2, "name": 1}
# Personal information that triggers the CCPA breach action: name plus one of these,
# or a username/email with its password (Cal. Civ. Code 1798.150 by reference to 1798.81.5).
CCPA_TRIGGERS = {"ssn", "drivers_license", "passport", "tax_id", "financial_account", "payment_card",
                 "medical", "health_insurance", "biometric", "credentials"}


LABELS = {"ssn": "Social Security numbers", "drivers_license": "driver's license or state ID numbers", "passport": "passport numbers",
          "tax_id": "taxpayer ID numbers", "financial_account": "financial account information", "payment_card": "payment card information",
          "medical": "medical information", "health_insurance": "health insurance information", "biometric": "biometric data",
          "credentials": "login credentials", "dob": "dates of birth", "name": "names", "email": "email addresses",
          "address": "postal addresses", "phone": "phone numbers"}


def detect_elements(text: str) -> dict[str, str]:
    """element -> the sentence fragment that names it (so each element is quotable)."""
    found: dict[str, str] = {}
    # Restrict to the part of a letter that says what was exposed when it exists;
    # boilerplate further down ("never share your SSN") otherwise inflates the list.
    m = re.search(r"what information was involved\??(.{0,1600})", text, re.I | re.S)
    scope = m.group(1) if m else text
    for el, rx in ELEMENTS:
        mm = re.search(rx, scope, re.I)
        if mm:
            # quote the whole sentence (or line) that names the element
            a = max(scope.rfind(".", 0, mm.start()), scope.rfind("\n", 0, mm.start())) + 1
            ends = [i for i in (scope.find(".", mm.end()), scope.find("\n", mm.end())) if i != -1]
            b = min(ends) + 1 if ends else len(scope)
            if b - a > 400:
                a, b = max(a, mm.start() - 150), min(b, mm.end() + 150)
            found[el] = re.sub(r"\s+", " ", scope[a:b]).strip()
    return found


# ------------------------------------------------------------- merging

_SUFFIX = re.compile(r"\b(incorporated|inc|llc|l\.l\.c|corp|corporation|co|company|ltd|lp|llp|plc|pc|pa|holdings?|group|the)\b\.?", re.I)


def norm_name(name: str) -> str:
    n = re.sub(r"\(.*?\)|“.*?”|\".*?\"", " ", name or "")
    n = n.replace("&", " and ")
    n = _SUFFIX.sub(" ", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n.lower())
    return re.sub(r"\s+", " ", n).strip()


def merge(notices: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for n in notices:
        if n.get("error"):
            continue
        groups.setdefault(norm_name(n["filer_name"]), []).append(n)
    return [build_incident(key, ns) for key, ns in groups.items()]


def _first(ns, key):
    for n in ns:
        v = n["structured"].get(key)
        if v:
            return v
    return None


def build_incident(key: str, ns: list[dict]) -> dict:
    ns = sorted(ns, key=lambda n: n["published_at"] or "")
    elements: dict[str, dict] = {}
    for i, n in enumerate(ns):
        for el, frag in detect_elements(n["notice_text"]).items():
            elements.setdefault(el, {"quote": frag, "notice": i})
        info_line = next((l for l in n["notice_text"].splitlines() if l.startswith("Information exposed")), None)
        for t in n["structured"].get("information_types", []):
            el = WA_TYPES.get(t)
            if el and info_line:
                elements[el] = {"quote": info_line, "notice": i}  # the AG's own field beats a regex hit
    sec = next((n for n in ns if n["source"] == "sec_8k"), None)
    wa = next((n for n in ns if n["source"] == "wa_ag"), None)
    ca = next((n for n in ns if n["source"] == "ca_ag"), None)
    affected_by_state = {}
    if wa and wa["structured"].get("wa_affected"):
        affected_by_state["WA"] = wa["structured"]["wa_affected"]
    total = _affected_total(ns)
    breach_dates = _first(ns, "breach_dates") or []
    industry = _industry(ns)
    first_public = ns[0]["published_at"]
    return {
        "incident_id": key.replace(" ", "-")[:60] + "-" + (first_public or "").replace("-", ""),
        "defendant": {
            "legal_name": (sec or ns[0])["filer_name"],
            "is_public": bool(sec),
            "cik": sec["structured"]["cik"] if sec else None,
            "ticker": sec["structured"].get("ticker") if sec else None,
            "state_of_incorporation": sec["structured"].get("state_of_incorporation") if sec else None,
            "principal_place_of_business_state": (sec["structured"].get("business_state") if sec else None) or (wa["structured"].get("entity_state") if wa else None),
            "industry": industry,
            "prior_breaches": None,
            "existing_suits": None,
        },
        "notices": [{k: n[k] for k in ("source", "source_url", "source_record_id", "published_at", "filer_name", "sample_letter_url")} for n in ns],
        "first_public_at": first_public,
        "breach_start": _first(ns, "breach_start") or (min(breach_dates) if breach_dates else None),
        "breach_end": _first(ns, "breach_end") or (max(breach_dates) if breach_dates else None),
        "discovered_at": _first(ns, "discovered_at"),
        "days_to_notify": _first(ns, "days_to_notify"),
        "attack_vector": _attack_vector(ns),
        "total_affected": total,
        "affected_by_state": affected_by_state,
        "states_with_notice": sorted({"CA"} if ca else set()) + (["WA"] if wa else []),
        "data_elements": sorted(elements, key=lambda e: -SENSITIVE.get(e, 0)),
        "data_element_evidence": elements,
        "_notices_full": ns,
    }


def _affected_total(ns) -> int | None:
    best = None
    for n in ns:
        for m in re.finditer(r"(?:approximately|affect(?:ed|s|ing)|impact(?:ed|ing)|of|notif(?:ied|ying))\s+([\d,]{4,})\s+(?:individuals|people|persons|patients|customers|consumers|members|current and former)", n["notice_text"], re.I):
            v = int(m.group(1).replace(",", ""))
            best = max(best or 0, v)
    return best


def _industry(ns) -> str | None:
    t = " ".join((n["structured"].get("industry") or "") + " " + (n["structured"].get("sic_description") or "") + " " + n["filer_name"] for n in ns).lower()
    body = " ".join(n["notice_text"][:4000] for n in ns).lower()
    if re.search(r"health|medical|hospital|dental|clinic|pharm|vision|diagnostic|physician", t) or "protected health information" in body or "hipaa" in body:
        return "healthcare"
    if re.search(r"financ|bank|credit|insur|brokerage|wealth|securities", t):
        return "finance"
    return None


def _attack_vector(ns) -> str | None:
    t = " ".join((n["structured"].get("cyberattack_type") or "") + " " + (n["structured"].get("cause") or "") + " " + n["notice_text"] for n in ns).lower()
    for label, rx in [("ransomware", r"ransomware|encrypted (?:certain|some) (?:of our )?systems"), ("phishing", r"phishing"),
                      ("vulnerability exploited", r"exploit(?:ed)? a vulnerability|vulnerability in"), ("credential compromise", r"compromised credentials|credential stuffing"),
                      ("third-party vendor", r"third[- ]party (?:vendor|service provider)|vendor"), ("misconfiguration", r"misconfigur"),
                      ("unauthorized access", r"unauthori[sz]ed (?:access|third party|actor)")]:
        if re.search(rx, t):
            return label
    return None


# ------------------------------------------------------------- triage

def triage(inc: dict, today: dt.date) -> dict:
    reasons: list[str] = []
    score = 0.0
    els = inc["data_elements"]
    w = sorted((SENSITIVE.get(e, 0) for e in els), reverse=True) + [0, 0, 0]
    s_data = min(35.0, w[0] + 0.4 * (w[1] + w[2]))
    score += s_data
    top = [e for e in els if SENSITIVE.get(e, 0) >= 12]
    reasons.append(f"data: {', '.join(els) or 'not stated in notice'} (+{s_data:.0f}/35)" + ("" if top else " — no high-sensitivity element; standing risk"))

    n = inc["total_affected"] or max(inc["affected_by_state"].values(), default=0) or None
    if n:
        s_size = min(20.0, 20 * math.log10(max(n, 1)) / 6)  # 1M people -> 20
        reasons.append(f"class size: {n:,} known (+{s_size:.0f}/20)")
    else:
        s_size = 6.0
        reasons.append("class size: not stated in any notice (+6/20 neutral)")
    score += s_size

    d = inc["defendant"]
    if d["is_public"]:
        s_col = 15.0
        reasons.append(f"defendant: SEC registrant {d.get('ticker') or d.get('cik')} — collectable, quotable 8-K (+15/15)")
    elif inc["affected_by_state"].get("WA", 0) > 50_000 or (inc["total_affected"] or 0) > 100_000:
        s_col = 10.0
        reasons.append("defendant: private, but a breach this size implies an enterprise (+10/15)")
    else:
        s_col = 6.0
        reasons.append("defendant: private, size unknown — check insurance/collectability (+6/15)")
    score += s_col

    ccpa = "CA" in inc["states_with_notice"] and bool(CCPA_TRIGGERS & set(els))
    if ccpa:
        score += 15
        reasons.append("California subclass with CCPA-qualifying data: statutory damages $100–$750 per consumer per incident (Cal. Civ. Code § 1798.150) (+15/15)")
    elif "CA" in inc["states_with_notice"]:
        score += 5
        reasons.append("California residents notified, but no CCPA-qualifying element named in the letter (+5/15)")

    late = inc["days_to_notify"]
    if late is None and inc.get("discovered_at") and inc["first_public_at"]:
        late = (dt.date.fromisoformat(inc["first_public_at"]) - dt.date.fromisoformat(inc["discovered_at"])).days
    if late is not None and late > 30:
        score += 10
        reasons.append(f"notice delay: {late} days elapsed before notification (+10/10)")
    elif late is not None:
        reasons.append(f"notice delay: {late} days (+0/10)")

    if d.get("industry") == "healthcare":
        score += 5
        reasons.append("healthcare defendant: HIPAA standards support negligence per se (+5)")

    age = (today - dt.date.fromisoformat(inc["first_public_at"])).days if inc["first_public_at"] else None
    if age is not None and age <= 3:
        score += 5
        reasons.append(f"public {age} day(s) ago — race still open (+5/5)")
    elif age is not None:
        reasons.append(f"public {age} days ago — check dockets before drafting (+0/5)")

    score = round(min(100.0, score))
    band = "file" if score >= 65 else "watch" if score >= 45 else "pass"
    return {"score": score, "band": band, "reasons": reasons, "ccpa_eligible": ccpa, "race_status": "unchecked (no docket source configured)"}
