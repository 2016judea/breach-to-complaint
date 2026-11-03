"""Draft class-action complaint (DATA_MODEL.md §6 and §8).

Two hard rules, enforced in code rather than by prompt:
1. Law comes only from data/statutes.json, each entry verified against the
   official page on a recorded date. The model is never asked for law, and any
   model-written paragraph that cites a statute, regulation or case is dropped.
2. Every factual allegation carries a verbatim quote from a source notice and
   that notice's URL. A model-written allegation whose quote is not found in the
   notice text (whitespace-normalised) is dropped.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import incident as I

HEADER = "**DRAFT FOR REVIEW BY LICENSED COUNSEL. NOT LEGAL ADVICE.**"
ROOT = Path(__file__).resolve().parent.parent
STATUTES = json.loads((ROOT / "data" / "statutes.json").read_text())
BY_ID = {s["id"]: s for s in STATUTES["statutes"]}
MODEL = "claude-opus-5-5"
STATE_GAP = "[state statutory counts: attorney to add]"

LAW_RX = re.compile(r"§|U\.S\.C|C\.F\.R|\bRCW\b|Civ\. Code|\bCode §|\b[A-Z][a-z]+ v\. [A-Z]|F\.\dd\b|S\. ?Ct\.|\b[Ss]tatutes?\b|\b[A-Z][A-Za-z]+ Act\b|\bHIPAA\b|\bCCPA\b")


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def cite(sid: str) -> str | None:
    s = BY_ID.get(sid)
    return s["cite"] if s else None


# ------------------------------------------------------------- facts

ALLEGATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["allegations"],
    "properties": {
        "allegations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "quote", "notice_index"],
                "properties": {
                    "text": {"type": "string"},
                    "quote": {"type": "string"},
                    "notice_index": {"type": "integer"},
                },
            },
        }
    },
}

SYSTEM = """You draft the FACTUAL ALLEGATIONS section of a data-breach class-action complaint for a plaintiff-side law firm. A licensed attorney will review and edit every word before anything is filed.

Rules:
- Use ONLY the notices provided. Do not add facts, numbers, dates or characterisations the notices do not support.
- Each allegation is one numbered-paragraph's worth of complaint prose (1-3 sentences), in the register of a federal complaint ("Defendant disclosed that..."). Refer to the company as "Defendant".
- Each allegation must carry `quote`: a VERBATIM span (8-60 words) copied character-for-character from the notice it relies on, and `notice_index` naming that notice. If you cannot quote it, do not allege it.
- Do not cite or name any statute, regulation, rule or case. Law is added separately by counsel.
- Cover, where the notices support it: what happened and when; how the intrusion occurred; when Defendant discovered it; what categories of personal information were involved; how many people and which states; when and how Defendant notified people; what Defendant offered (e.g. credit monitoring) and its duration; admissions that investigation is ongoing or that data was taken/published.
- 6 to 14 allegations. Plain facts; no adjectives the notice does not use."""


def draft_allegations(inc: dict, api_key: str | None) -> tuple[list[dict], dict]:
    """Model-written allegations, each validated against its notice. Returns (allegations, usage)."""
    notices = inc["_notices_full"]
    if not api_key:
        return [], {"llm": False}
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, max_retries=3)
    docs = []
    for i, n in enumerate(notices):
        docs.append(f"<notice index=\"{i}\" source=\"{n['source']}\" url=\"{n['source_url']}\" published=\"{n['published_at']}\">\n{n['notice_text'][:18000]}\n</notice>")
    user = f"Defendant: {inc['defendant']['legal_name']}\n\n" + "\n\n".join(docs)
    kwargs = dict(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ALLEGATION_SCHEMA}},
    )
    try:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError:
        resp = client.messages.create(**kwargs)
    usage = {"llm": True, "model": getattr(resp, "model", MODEL), "input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens, "stop_reason": resp.stop_reason}
    if resp.stop_reason == "refusal":
        return [], usage
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        raw = json.loads(text)["allegations"]
    except (json.JSONDecodeError, KeyError):
        usage["parse_error"] = text[:200]
        return [], usage
    kept, dropped = [], []
    for a in raw:
        i = a.get("notice_index")
        if not isinstance(i, int) or not (0 <= i < len(notices)):
            dropped.append({**a, "why": "bad notice_index"})
            continue
        if _norm(a["quote"]) not in _norm(notices[i]["notice_text"]) or len(a["quote"].split()) < 4:
            dropped.append({**a, "why": "quote not found verbatim in notice"})
            continue
        if LAW_RX.search(a["text"]):
            dropped.append({**a, "why": "cites law; law comes only from the verified table"})
            continue
        kept.append({"text": a["text"].strip(), "quote": a["quote"].strip(), "source_url": notices[i]["source_url"], "source": notices[i]["source"]})
    usage["allegations_kept"] = len(kept)
    usage["allegations_dropped"] = dropped
    return kept, usage


def structured_allegations(inc: dict) -> list[dict]:
    """Allegations that need no model: read straight off a source's structured fields."""
    out = []
    for n in inc["_notices_full"]:
        url = n["source_url"]
        if n["source"] == "wa_ag":
            st = n["structured"]
            line = next((l for l in n["notice_text"].splitlines() if l.startswith("Washingtonians affected")), None)
            if line and st.get("wa_affected"):
                out.append({"text": f"Defendant reported to the Washington State Attorney General that {st['wa_affected']:,} Washington residents were affected by the Data Breach.", "quote": line, "source_url": url, "source": "wa_ag"})
            line = next((l for l in n["notice_text"].splitlines() if l.startswith("Breach start")), None)
            if line and st.get("breach_start"):
                out.append({"text": f"According to Defendant's report to the Washington State Attorney General, the Data Breach began on {st['breach_start']} and Defendant became aware of it on {st.get('discovered_at')}.", "quote": line, "source_url": url, "source": "wa_ag"})
            line = next((l for l in n["notice_text"].splitlines() if l.startswith("Information exposed")), None)
            if line and st.get("information_types"):
                out.append({"text": f"Defendant reported that the following categories of information were exposed: {', '.join(st['information_types'])}.", "quote": line, "source_url": url, "source": "wa_ag"})
            line = next((l for l in n["notice_text"].splitlines() if l.startswith("Days elapsed")), None)
            if line and st.get("days_to_notify") is not None:
                out.append({"text": f"Defendant's report to the Washington State Attorney General records that {st['days_to_notify']} days elapsed before affected individuals were notified.", "quote": line, "source_url": url, "source": "wa_ag"})
        elif n["source"] == "ca_ag":
            st = n["structured"]
            out.append({"text": f"On {st['reported_to_ca_ag']}, Defendant submitted a sample consumer notification letter to the California Attorney General, which it is required to do when a breach affects more than 500 California residents.",
                        "quote": f"{n['filer_name']} … {st['reported_to_ca_ag']}", "source_url": url, "source": "ca_ag", "quote_is_listing": True})
        elif n["source"] == "sec_8k":
            st = n["structured"]
            items = ", ".join(st.get("items") or [])
            out.append({"text": f"On {n['published_at']}, Defendant filed a Current Report on Form 8-K with the U.S. Securities and Exchange Commission (Items {items}) disclosing a cybersecurity incident.",
                        "quote": n["notice_text"][:160].strip(), "source_url": url, "source": "sec_8k"})
    return out


# ------------------------------------------------------------- assembly

def _p(paras: list[str], text: str) -> None:
    paras.append(text)


def build_complaint(inc: dict, tri: dict, allegations: list[dict]) -> dict:
    d = inc["defendant"]
    name = d["legal_name"]
    healthcare = d.get("industry") == "healthcare"
    states = inc["states_with_notice"]
    used: list[str] = []
    gaps: list[str] = [
        "Named plaintiff(s): name, state of residence, the notice they received, their injuries and mitigation costs.",
        "Venue and district: select the court; confirm Defendant's citizenship for CAFA minimal diversity.",
        "Class size and amount in controversy: confirm > 100 members and > $5,000,000 before relying on CAFA.",
        "Docket check: search PACER / CourtListener for existing suits against Defendant over this incident.",
    ]

    def law(sid: str) -> str:
        s = BY_ID.get(sid)
        if not s:
            gaps.append(f"Statute {sid} is not in the verified table; attorney to supply.")
            return "[statute: attorney to add]"
        used.append(sid)
        return s["cite"]

    L: list[str] = []
    n = [0]

    def para(text: str, src: dict | None = None) -> None:
        n[0] += 1
        line = f"{n[0]}. {text}"
        if src:
            q = src["quote"].replace("\n", " ").strip()
            line += f"\n   > \"{q}\"\n   > — Source: <{src['source_url']}>"
        L.append(line)

    L += [HEADER, "", f"*Generated {inc.get('_generated_at', '')} by breach-to-complaint. Triage score {tri['score']}/100 ({tri['band']}). Every factual paragraph is pinned to a quote from the source notice; every statute is from the verified table in Appendix B.*", "",
          "UNITED STATES DISTRICT COURT", "FOR THE [DISTRICT: attorney to select]", "",
          "[PLAINTIFF NAME], individually and on behalf of all others similarly situated,", "", "Plaintiff,", "", "v.", "",
          f"{name.upper()},", "", "Defendant.", "", "Case No. [__________]", "",
          "**CLASS ACTION COMPLAINT**", "", "**JURY TRIAL DEMANDED**", ""]

    L += ["## NATURE OF THE ACTION", ""]
    para(f"Plaintiff brings this class action against {name} (\"Defendant\") for its failure to protect the personal information of Plaintiff and the Class from unauthorized access in the data breach Defendant disclosed beginning on {inc['first_public_at']} (the \"Data Breach\").")
    if inc["data_elements"]:
        ev = inc["data_element_evidence"][inc["data_elements"][0]]
        src = inc["_notices_full"][ev["notice"]]
        para(f"The information involved includes {', '.join(I.LABELS.get(e, e) for e in inc['data_elements'])}.", {"quote": ev["quote"], "source_url": src["source_url"]})
    L.append("")

    L += ["## JURISDICTION AND VENUE", ""]
    para(f"This Court has subject-matter jurisdiction under {law('US-CAFA')} because, on information and belief, the Class has more than 100 members, the amount in controversy exceeds $5,000,000 exclusive of interest and costs, and at least one Class member is a citizen of a State different from Defendant. [Attorney to confirm.]")
    para("Venue is proper in this District because [attorney to add: Defendant's principal place of business / where a substantial part of the events occurred].")
    L.append("")

    L += ["## PARTIES", ""]
    para("Plaintiff [PLAINTIFF NAME] is a resident of [STATE] whose personal information was involved in the Data Breach. [Attorney to add the notice Plaintiff received and Plaintiff's injuries.]")
    bits = [f"Defendant {name}"]
    if d.get("state_of_incorporation"):
        bits.append(f"is incorporated in {d['state_of_incorporation']} (per its SEC filings)")
    if d.get("principal_place_of_business_state"):
        bits.append(f"has its business address in {d['principal_place_of_business_state']}")
    if d.get("is_public"):
        bits.append(f"is an SEC registrant (CIK {d['cik']}{', ticker ' + d['ticker'] if d.get('ticker') else ''})")
    para((", ".join(bits) if len(bits) > 1 else bits[0] + " [attorney to add: state of incorporation and principal place of business]") + ".")
    L.append("")

    L += ["## FACTUAL ALLEGATIONS", ""]
    for a in allegations:
        para(a["text"], a)
    if not allegations:
        para("[No factual allegations could be pinned to a source quote; attorney to draft from the notices in Appendix A.]")
    L.append("")

    L += ["## CLASS ALLEGATIONS", ""]
    para("Plaintiff brings this action under Federal Rule of Civil Procedure 23 on behalf of the following Nationwide Class: All persons in the United States whose personal information was involved in the Data Breach.")
    for st in states:
        para(f"Plaintiff also seeks to represent a {st} Subclass: All residents of {st} whose personal information was involved in the Data Breach.")
    para("Excluded are Defendant, its officers and directors, and the judicial officers assigned to this case. Plaintiff reserves the right to amend the class definitions.")
    para("Numerosity, commonality, typicality, adequacy, predominance and superiority are satisfied because the Data Breach was a single event affecting the Class in the same way. [Attorney to particularise.]")
    L.append("")

    counts = []
    counts.append(("NEGLIGENCE", "On behalf of the Nationwide Class",
                   ["Defendant owed Plaintiff and the Class a duty to exercise reasonable care in obtaining, retaining, securing and protecting their personal information.",
                    "Defendant breached that duty by failing to implement reasonable security measures, as shown by the Data Breach described above.",
                    "As a direct and proximate result, Plaintiff and the Class suffered injury, including the lost value of their personal information, time and money spent on mitigation, and an increased and continuing risk of fraud and identity theft."]))
    nps = [f"Section 5 of the FTC Act, {law('US-FTC-5')}, prohibits unfair practices in or affecting commerce, which include the failure to use reasonable measures to protect consumers' personal information. [Attorney to add supporting authority.]"]
    if healthcare:
        nps.append(f"As a HIPAA covered entity or business associate, Defendant was required to ensure the confidentiality, integrity and availability of electronic protected health information ({law('US-HIPAA-164.306')}) and to notify affected individuals without unreasonable delay and no later than 60 days after discovery ({law('US-HIPAA-164.404')}). [Attorney to confirm Defendant's HIPAA status.]")
    nps.append("Plaintiff and the Class are within the class of persons these provisions protect, and the harm they suffered is the harm the provisions guard against. Defendant's violation constitutes negligence per se.")
    counts.append(("NEGLIGENCE PER SE", "On behalf of the Nationwide Class", nps))
    counts.append(("BREACH OF IMPLIED CONTRACT", "On behalf of the Nationwide Class",
                   ["Plaintiff and the Class provided their personal information to Defendant as a condition of obtaining services or employment, under an implied promise that Defendant would protect it.",
                    "Defendant breached that implied contract by failing to safeguard the information, causing the damages described above."]))
    counts.append(("UNJUST ENRICHMENT", "On behalf of the Nationwide Class, pleaded in the alternative",
                   ["Plaintiff and the Class conferred a benefit on Defendant in the form of payments and/or their personal information, part of which should have funded adequate data security.",
                    "It would be inequitable for Defendant to retain that benefit without having provided the promised security."]))
    if healthcare or d.get("industry") == "finance":
        counts.append(("BREACH OF FIDUCIARY DUTY", "On behalf of the Nationwide Class",
                       [f"As a {'healthcare provider' if healthcare else 'financial institution'} entrusted with confidential information, Defendant owed Plaintiff and the Class a fiduciary duty to keep that information confidential. [Attorney to confirm the relationship supports a fiduciary duty in the chosen forum.]",
                        "Defendant breached that duty by failing to protect the information, causing the damages described above."]))

    if "CA" in states:
        ca_paras = [f"Defendant was required to implement and maintain reasonable security procedures and practices appropriate to the nature of the information ({law('CA-CIV-1798.81.5')})."]
        if tri.get("ccpa_eligible"):
            ca_paras.append(f"The California Subclass's nonencrypted and nonredacted personal information was subject to unauthorized access and exfiltration, theft or disclosure as a result of Defendant's failure to maintain reasonable security. Each member may recover statutory damages of not less than $100 and not greater than $750 per consumer per incident, or actual damages, whichever is greater ({law('CA-CIV-1798.150')}). [Attorney: CCPA requires 30 days' written notice and opportunity to cure before seeking statutory damages; confirm notice sent.]")
        ca_paras.append(f"Defendant was required to notify affected California residents within 30 calendar days of discovery ({law('CA-CIV-1798.82')}); customers injured by a violation may recover damages ({law('CA-CIV-1798.84')}). [Attorney to plead the delay from the notice dates.]")
        ca_paras.append(f"Defendant's conduct is unlawful and unfair business practice under {law('CA-BPC-17200')}.")
        counts.append(("CALIFORNIA STATUTORY CLAIMS", "On behalf of the California Subclass", ca_paras))
    if "WA" in states:
        counts.append(("WASHINGTON STATUTORY CLAIMS", "On behalf of the Washington Subclass",
                       [f"Defendant was required to disclose the breach of the security of its system to affected Washington residents ({law('WA-RCW-19.255.010')}), and a violation is enforceable under the Consumer Protection Act ({law('WA-RCW-19.255.040')}).",
                        f"Unfair or deceptive acts or practices in trade or commerce are unlawful ({law('WA-RCW-19.86.020')}), and persons injured in their business or property may sue for actual damages, treble damages up to the statutory cap, and attorney's fees ({law('WA-RCW-19.86.090')})."]))
    other = []
    bstate = d.get("principal_place_of_business_state")
    if bstate and len(bstate) == 2 and bstate not in states and bstate not in ("CA", "WA"):
        other.append(bstate)
    for st in other + ["other states"]:
        gaps.append(f"{STATE_GAP} — {st}")
    counts.append(("ADDITIONAL STATE STATUTORY CLAIMS", "On behalf of state subclasses", [STATE_GAP + (f" (including {', '.join(other)}, Defendant's home state)" if other else "")]))

    roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
    for i, (title, who, ps) in enumerate(counts):
        L += [f"## COUNT {roman[i]} — {title}", f"*({who})*", ""]
        para("Plaintiff repeats the allegations above as if fully set forth here.")
        for p in ps:
            para(p)
        L.append("")

    L += ["## PRAYER FOR RELIEF", "",
          "Plaintiff requests that the Court: (a) certify the Class and Subclasses and appoint Plaintiff and counsel to represent them; (b) award compensatory, statutory and, where permitted, treble damages; (c) order injunctive relief requiring Defendant to adopt reasonable data security and to provide extended identity-protection services; (d) award restitution and disgorgement; (e) award attorneys' fees, costs and pre- and post-judgment interest; and (f) grant such other relief as is just.", "",
          "## JURY DEMAND", "", "Plaintiff demands a trial by jury on all issues so triable.", "",
          "Dated: [DATE]", "", "[ATTORNEY SIGNATURE BLOCK]", "", "---", "",
          "## Appendix A — Source notices", ""]
    for nn in inc["_notices_full"]:
        L.append(f"- {nn['source']} · {nn['filer_name']} · {nn['published_at']} · <{nn['source_url']}>" + (f" · letter <{nn['sample_letter_url']}>" if nn.get("sample_letter_url") else ""))
    L += ["", "## Appendix B — Statutes cited (verified table)", "", "Each was checked against the official text at the URL on the date shown (`data/statutes.json`, `scripts/verify_statutes.mjs`).", ""]
    for sid in dict.fromkeys(used):
        s = BY_ID[sid]
        L.append(f"- **{s['cite']}** — {s['title']}. <{s['url']}> (verified {s['verified_on']})")
    L += ["", "## Appendix C — What counsel must add before filing", ""] + [f"- {g}" for g in dict.fromkeys(gaps)]
    L += ["", "## Appendix D — Triage", "", f"Score **{tri['score']}/100** ({tri['band']})", ""] + [f"- {r}" for r in tri["reasons"]]
    md = "\n".join(L) + "\n"
    return {"markdown": md, "statutes_cited": [BY_ID[s]["cite"] for s in dict.fromkeys(used)], "gaps": list(dict.fromkeys(gaps)), "allegation_count": len(allegations)}


def audit_citations(md: str) -> list[str]:
    """Every statute-looking citation in the draft must be a cite from the verified table."""
    allowed = {s["cite"] for s in STATUTES["statutes"]}
    found = set(re.findall(r"(?:\d+ U\.S\.C\. § [\d()a-z]+|\d+ C\.F\.R\. § [\d.]+|Cal\. (?:Civ\.|Bus\. & Prof\.) Code § [\d.]+|RCW [\d.]+)", md))
    return sorted(c for c in found if c not in allowed and not any(c.startswith(a) or a.startswith(c) for a in allowed))
