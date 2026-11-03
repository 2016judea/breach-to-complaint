"""Read breach notices from public sources and return them as Notice dicts.

Each source returns a list of dicts with the same keys (see DATA_MODEL.md §1):
source, source_url, source_record_id, published_at, filer_name, notice_text,
sample_letter_url, plus source-specific structured fields under `structured`.

Sources shipped:
  sec_8k  SEC EDGAR full-text search, 8-K filings that cite Item 1.05
          (material cybersecurity incident) or describe a cybersecurity incident.
  ca_ag   California AG breach list (oag.ca.gov/privacy/databreach/list) and the
          sample consumer letter PDF each entry links.
  wa_ag   Washington AG breach notices, published as open data on data.wa.gov
          (Socrata sb4j-ca4h + padd-mby7 for the data elements).
"""
from __future__ import annotations

import datetime as dt
import html
import io
import re
import time
from typing import Iterable

import requests

SEC_UA = "breach-to-complaint research tool aidan@brickandmortar.dev"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

_session = requests.Session()


def _get(url: str, ua: str = BROWSER_UA, **kw) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            r = _session.get(url, headers={"User-Agent": ua, **kw.pop("headers", {})}, timeout=60, **kw)
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 + 3 * attempt)
                last = r
                continue
            return r
        except requests.RequestException as e:  # network blip
            last = e
            time.sleep(2 + 3 * attempt)
    if isinstance(last, requests.Response):
        return last
    raise last  # type: ignore[misc]


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def pdf_text(url: str) -> str:
    from pypdf import PdfReader

    r = _get(url)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return ""
    reader = PdfReader(io.BytesIO(r.content))
    pages = [(p.extract_text() or "") for p in reader.pages[:12]]
    text = "\n".join(pages)
    return re.sub(r"[ \t]+", " ", text).strip()


# ---------------------------------------------------------------- SEC 8-K

def sec_8k(since: dt.date, until: dt.date, limit: int = 50) -> list[dict]:
    """8-Ks mentioning a cybersecurity incident, filed in [since, until]."""
    seen: dict[str, dict] = {}
    queries = ['"Item 1.05"', '"cybersecurity incident"']
    for q in queries:
        url = (
            "https://efts.sec.gov/LATEST/search-index?q=" + requests.utils.quote(q)
            + f"&forms=8-K&dateRange=custom&startdt={since}&enddt={until}"
        )
        r = _get(url, ua=SEC_UA)
        if r.status_code != 200:
            continue
        for h in r.json().get("hits", {}).get("hits", []):
            s = h["_source"]
            adsh = s["adsh"]
            items = s.get("items") or []
            # Item 1.05 filings always count; an 8.01 mention counts only via the
            # cybersecurity query, and we confirm the words in the document below.
            if adsh in seen:
                continue
            cik = s["ciks"][0].lstrip("0")
            fname = h["_id"].split(":", 1)[1]
            seen[adsh] = {
                "adsh": adsh, "cik": cik, "file_date": s["file_date"], "items": items,
                "name": re.sub(r"\s+\(CIK.*$", "", s["display_names"][0]).strip(),
                "doc_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}",
                "index_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/",
                "root_form": s.get("root_forms", ["8-K"])[0],
            }
        time.sleep(0.3)

    out = []
    for f in sorted(seen.values(), key=lambda x: x["file_date"], reverse=True):
        if len(out) >= limit:
            break
        r = _get(f["doc_url"], ua=SEC_UA)
        time.sleep(0.15)
        if r.status_code != 200:
            continue
        text = _strip_html(r.text)
        body = _sec_incident_passage(text)
        if not body:
            continue  # the words matched an exhibit or boilerplate risk factor, not an incident report
        name, ticker = _split_ticker(f["name"])
        meta = sec_company(f["cik"])
        out.append({
            "source": "sec_8k",
            "source_url": f["doc_url"],
            "source_record_id": f["adsh"],
            "published_at": f["file_date"],
            "filer_name": meta.get("name") or name,
            "notice_text": body,
            "sample_letter_url": None,
            "structured": {
                "cik": f["cik"], "ticker": ticker or (meta.get("tickers") or [None])[0],
                "items": f["items"], "filing_index": f["index_url"],
                "state_of_incorporation": meta.get("stateOfIncorporation"),
                "business_state": meta.get("business_state"),
                "sic_description": meta.get("sicDescription"),
            },
        })
    return out


def _split_ticker(display: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)\s+\(([A-Z.\-, ]+)\)\s*$", display)
    if m:
        return m.group(1).strip(), m.group(2).split(",")[0].strip()
    return display, None


_SEC_START = re.compile(r"Item\s*1\.05\.?\s*Material\s+Cybersecurity\s+Incidents?|Item\s*8\.01\.?\s*Other\s+Events", re.I)
_SEC_END = re.compile(r"\n\s*Item\s*\d\.\d\d|\n\s*SIGNATURES?\b|Forward[- ]Looking Statements", re.I)


def _sec_incident_passage(text: str) -> str:
    """The Item 1.05 / 8.01 body, if it actually describes a cyber incident."""
    best = ""
    for m in _SEC_START.finditer(text):
        rest = text[m.start():]
        end = _SEC_END.search(rest, 40)
        chunk = rest[: end.start() if end else 8000].strip()
        if re.search(r"cyber|unauthori[sz]ed|ransomware|threat actor|breach|incident", chunk, re.I) and len(chunk) > len(best):
            best = chunk
    return best[:12000]


_company_cache: dict[str, dict] = {}


def sec_company(cik: str) -> dict:
    if cik in _company_cache:
        return _company_cache[cik]
    r = _get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", ua=SEC_UA)
    meta: dict = {}
    if r.status_code == 200:
        j = r.json()
        meta = {k: j.get(k) for k in ("name", "tickers", "stateOfIncorporation", "sicDescription")}
        meta["business_state"] = ((j.get("addresses") or {}).get("business") or {}).get("stateOrCountry")
    _company_cache[cik] = meta
    return meta


# ---------------------------------------------------------------- California AG

CA_LIST = "https://oag.ca.gov/privacy/databreach/list"


def ca_ag(since: dt.date, until: dt.date, limit: int = 50, fetch_letters: bool = True) -> list[dict]:
    out: list[dict] = []
    page = 0
    while len(out) < limit and page < 20:
        r = _get(CA_LIST + (f"?page={page}" if page else ""))
        if r.status_code != 200:
            break
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S)[1:]
        if not rows:
            break
        stop = False
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(cells) < 3:
                continue
            link = re.search(r'href="([^"]+)"', cells[0])
            name = _strip_html(cells[0])
            breach_dates = re.findall(r"\d{2}/\d{2}/\d{4}", cells[1])
            reported = re.findall(r"\d{2}/\d{2}/\d{4}", cells[2])
            if not reported:
                continue
            rep = dt.datetime.strptime(reported[0], "%m/%d/%Y").date()
            if rep > until:
                continue
            if rep < since:
                stop = True
                break
            detail = link.group(1) if link else None
            if detail and detail.startswith("/"):
                detail = "https://oag.ca.gov" + detail
            out.append({
                "source": "ca_ag",
                "source_url": detail or CA_LIST,
                "source_record_id": (detail or name).rsplit("/", 1)[-1],
                "published_at": rep.isoformat(),
                "filer_name": name,
                "notice_text": "",
                "sample_letter_url": None,
                "structured": {
                    "breach_dates": [dt.datetime.strptime(d, "%m/%d/%Y").date().isoformat() for d in breach_dates],
                    "reported_to_ca_ag": rep.isoformat(),
                    "ca_residents_affected": True,  # filing with the CA AG means >500 CA residents (Civ. Code 1798.82(f))
                },
            })
            if len(out) >= limit:
                break
        if stop:
            break
        page += 1
        time.sleep(0.5)

    if fetch_letters:
        for n in out:
            _ca_fill_letter(n)
    return out


def _ca_fill_letter(n: dict) -> None:
    if not n["source_url"].startswith("https://oag.ca.gov/ecrime"):
        return
    r = _get(n["source_url"])
    if r.status_code != 200:
        return
    pdfs = re.findall(r'href="(https://oag\.ca\.gov/system/files/[^"]+\.pdf)"', r.text, re.I)
    if not pdfs:
        return
    n["sample_letter_url"] = pdfs[0]
    try:
        n["notice_text"] = pdf_text(pdfs[0])[:20000]
    except Exception as e:  # a malformed PDF should not sink the run
        n["structured"]["letter_error"] = str(e)[:200]
    time.sleep(0.4)


# ---------------------------------------------------------------- Washington AG

WA_NOTICES = "https://data.wa.gov/resource/sb4j-ca4h.json"
WA_ELEMENTS = "https://data.wa.gov/resource/padd-mby7.json"
WA_PAGE = "https://data.wa.gov/d/sb4j-ca4h"


def wa_ag(since: dt.date, until: dt.date, limit: int = 50) -> list[dict]:
    q = {
        "$where": f"datesubmitted between '{since}T00:00:00' and '{until}T23:59:59'",
        "$order": "datesubmitted DESC",
        "$limit": str(limit),
    }
    r = _get(WA_NOTICES, params=q)
    if r.status_code != 200:
        return []
    rows = r.json()
    ids = [x["id"] for x in rows]
    elements: dict[str, list[str]] = {}
    if ids:
        id_list = ",".join(f"'{i}'" for i in ids)
        e = _get(WA_ELEMENTS, params={"$where": f"id in ({id_list})", "$select": "id,informationtype", "$limit": "5000"})
        if e.status_code == 200:
            for x in e.json():
                if x.get("informationtype"):
                    elements.setdefault(x["id"], []).append(x["informationtype"])
    out = []
    for x in rows:
        d = lambda k: (x.get(k) or "")[:10] or None  # noqa: E731
        affected = int(x["washingtoniansaffected"]) if x.get("washingtoniansaffected", "").isdigit() else None
        lines = [
            f"Organization: {x.get('name')}",
            f"Date submitted to Washington AG: {d('datesubmitted')}",
            f"Breach start: {d('datestart')}; breach end: {d('dateend')}; date organization became aware: {d('dateaware')}",
            f"Washingtonians affected: {x.get('washingtoniansaffected')}",
            f"Cause: {x.get('databreachcause')}" + (f" ({x['cyberattacktype']})" if x.get("cyberattacktype") else ""),
            f"Industry: {x.get('industrytype')}" + (f" / {x['businesstype']}" if x.get("businesstype") else ""),
            f"Information exposed: {', '.join(sorted(set(elements.get(x['id'], [])))) or 'not listed'}",
            f"Days elapsed before notification: {x.get('dayselapsedbeforenotification')}",
        ]
        out.append({
            "source": "wa_ag",
            "source_url": f"{WA_NOTICES}?id={x['id']}",
            "source_record_id": x["id"],
            "published_at": d("datesubmitted"),
            "filer_name": x.get("name"),
            "notice_text": "\n".join(lines),
            "sample_letter_url": None,
            "structured": {
                "wa_affected": affected,
                "breach_start": d("datestart"), "breach_end": d("dateend"), "discovered_at": d("dateaware"),
                "cause": x.get("databreachcause"), "cyberattack_type": x.get("cyberattacktype"),
                "industry": x.get("industrytype"), "business_type": x.get("businesstype"),
                "entity_state": x.get("entitystate"),
                "information_types": sorted(set(elements.get(x["id"], []))),
                "days_to_notify": int(x["dayselapsedbeforenotification"]) if str(x.get("dayselapsedbeforenotification", "")).isdigit() else None,
                "dataset_page": WA_PAGE,
            },
        })
    return out


SOURCES = {"sec_8k": sec_8k, "ca_ag": ca_ag, "wa_ag": wa_ag}


def fetch(sources: Iterable[str], since: dt.date, until: dt.date, limit: int) -> list[dict]:
    notices: list[dict] = []
    for s in sources:
        fn = SOURCES.get(s)
        if not fn:
            continue
        try:
            notices.extend(fn(since, until, limit))
        except Exception as e:  # one dead source must not kill the run
            notices.append({"source": s, "error": str(e)[:300]})
    return notices
