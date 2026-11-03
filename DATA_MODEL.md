# Data model — breach-to-complaint

Written 2026-11-03 from the need alone, before looking at any source
(`~/.claude/BUSINESS.md` rule 5). The customer is a plaintiff-side class-action
firm. The job: a breach goes public, and within minutes the firm knows what
happened, whether it is worth filing, and holds a draft complaint an attorney can
edit and file. Each field names the question it answers for the firm.

## 1. Notice — one public disclosure of a breach

| field | question it answers |
|---|---|
| source | Where did this surface (SEC 8-K, state AG, HHS OCR, leak site)? How fast is that channel? |
| source_url | Where is the primary document the complaint's facts will cite? |
| source_record_id | Is this the same notice we already saw? |
| published_at | How long has this been public — how many hours do we have before others file? |
| filer_name | Who disclosed it, as written on the notice? |
| notice_text | What exactly did the defendant admit, in its own words (quotable)? |
| sample_letter_url | What did consumers actually receive? |

## 2. Incident — one breach, possibly disclosed in many notices

| field | question it answers |
|---|---|
| incident_id | Are these five state notices the same breach? |
| notices[] | Which disclosures make up this incident (and their URLs)? |
| breach_start / breach_end | When did the exposure happen — does the limitations clock matter? |
| discovered_at | How long did the defendant sit on it before discovering it? |
| notified_at | How long between discovery and consumer notice (delay = a count)? |
| attack_vector | Ransomware, phishing, misconfiguration, vendor? Goes to negligence. |
| third_party_vendor | Is a vendor the better (or additional) defendant? |
| ransomware_group / leak_site_post | Was data published or sold — is harm already concrete? |

## 3. Affected population

| field | question it answers |
|---|---|
| total_affected | How big is the class? |
| affected_by_state{state: count} | Which state subclasses exist (CA residents → CCPA statutory damages)? |
| affected_minors | Is there a more sympathetic subclass? |

## 4. Data elements exposed

| field | question it answers |
|---|---|
| elements[] (SSN, DL/state ID, financial account, card, medical, health insurance, biometric, credentials, DOB, name, email, passport) | Is the data the kind courts treat as concrete injury (SSN, medical) or not (email only)? Which statutes trigger (CCPA 1798.150 needs specific elements)? |
| encrypted | Does the defendant have an encryption safe harbour? |

## 5. Defendant

| field | question it answers |
|---|---|
| legal_name | Who goes on the caption? |
| state_of_incorporation / principal_place_of_business | Where is diversity / CAFA jurisdiction and venue? |
| is_public, cik, ticker | Is it collectable, and is there an SEC disclosure to quote? |
| industry (healthcare → HIPAA) | Which negligence-per-se standard applies (FTC Act §5, HIPAA)? |
| prior_breaches[] | Was it on notice — does a repeat breach strengthen negligence? |
| existing_suits[] (court, docket, filed_at) | Has someone already filed? Are we first, or joining a consolidation fight? |

## 6. Candidate claims

| field | question it answers |
|---|---|
| claim | Which count (negligence, negligence per se, implied contract, unjust enrichment, fiduciary duty, state statute)? |
| statute_id → verified statute table | What law does it rest on, verified against the legislature's own page on a known date? |
| elements_supported_by[] | Which facts on the notice support each element? |
| subclass | Nationwide, or a state subclass? |

## 7. Triage score

| field | question it answers |
|---|---|
| score 0–100 | Should we spend an attorney hour on this today? |
| reasons[] | Why, in one line each, so a partner can overrule it in ten seconds? |
| race_status | Are we first to file? |

## 8. Draft complaint

| field | question it answers |
|---|---|
| markdown | What does the attorney open and edit? |
| allegations[] {text, quote, source_url} | Can every factual allegation be traced to the defendant's own words? |
| statutes_cited[] | Is every cited section in the verified table (no model-memory law)? |
| gaps[] | What must the attorney add before filing (named plaintiffs, state counts we haven't verified)? |

---

## Reality check (2026-11-03, measured against live sources)

Filled = a source we can read today carries the field for a material share of
records. See `scripts/` and `examples/` for the reads.

Sample: every notice published 2026-10-13 → 2026-11-03 across the three shipped
sources, 15 incidents (CA AG 9, SEC 8-K 4, WA AG 2). Triage spread on that sample:
18 → 74 (5 file, 7 watch, 3 pass), so the score separates incidents rather than
stamping them all the same.

| field | status | source / why not |
|---|---|---|
| Notice: source, url, record id, published_at, filer_name | **filled** | all three sources |
| notice_text | **filled** | 8-K Item 1.05/8.01 body; CA AG sample letter PDF (pypdf); WA AG structured row rendered as text |
| sample_letter_url | **filled for CA** | CA AG attaches the PDF; SEC and WA have none |
| incident_id, notices[] | **filled** | name-normalised merge across sources in one run |
| breach_start / breach_end | **filled 10 of 13 (first pass)** | CA list dates, WA fields; 8-Ks rarely state them |
| discovered_at | **partial** | WA field; CA letters in prose (the model quotes it, not parsed into the field) |
| notified_at / delay | **partial** | WA `dayselapsedbeforenotification`; CA/SEC only in prose |
| attack_vector | **filled 9 of 13 (first pass)** | regex over notice text + WA cause field |
| third_party_vendor | **partial** | only when the letter says "vendor" |
| ransomware_group / leak_site_post | **NOT FILLED** | no leak-site source shipped; leak sites are criminal infrastructure and not something this actor should scrape |
| total_affected | **NOT FILLED (0 of 15)** | none of the three sources states a national count. Maine AG and HHS OCR do: Maine's list was not probed; HHS OCR's portal returned 0 bytes to a scripted GET |
| affected_by_state | **WA only** | WA AG gives the WA count; CA AG implies >500 CA residents but gives no number |
| affected_minors | **NOT FILLED** | no source carries it |
| data elements | **filled 12 of 13 (first 13-incident pass)** | WA structured field; CA letter "What information was involved" section; 8-Ks mostly silent |
| encrypted | **NOT FILLED** | prose only; not parsed |
| defendant legal_name | **filled** | |
| state_of_incorporation / principal place | **public cos only** | SEC submissions JSON; WA `entitystate` sometimes |
| is_public, cik, ticker | **filled** | SEC sources only; a CA/WA notice from a public company is not yet joined to EDGAR |
| industry | **filled (heuristic)** | WA industry field, SEC SIC, name/text keywords |
| prior_breaches | **NOT FILLED** | would need the AG lists' history per defendant; not built |
| existing_suits / race_status | **NOT FILLED** | CourtListener API needs a token (401 keyless, HTML search 403); PACER is paid. Draft tells counsel to check dockets |
| candidate claims + statute | **filled** | 13 sections verified against official pages 2026-11-03 (`data/statutes.json`); states other than CA and WA print `[state statutory counts: attorney to add]` |
| triage score + reasons | **filled** | `src/incident.py` |
| draft: markdown, allegations with quote+URL, statutes_cited, gaps | **filled** | `src/draft.py`; quotes checked verbatim, law-citing model text dropped |

Call: **reframe, not abandon.** The model's core — notice → pinned facts → verified
claims → draft — is fully fillable. The two fields that fail and matter most to a
firm racing to file are `total_affected` (class size drives the fee) and
`existing_suits` (are we first). Both have named sources (Maine AG / HHS OCR;
CourtListener with a token), so they are the next slice, not a reason to stop. The
draft and triage say plainly when they are missing rather than guessing.
