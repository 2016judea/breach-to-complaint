# breach-to-complaint

**Live:** not yet on the Apify Store. Publishing is one command (`scripts/publish.sh`) once an Apify API token exists; see *Status*.

A data breach goes public. Within minutes a plaintiff-side class-action firm gets:

1. **An incident record**: defendant, dates, what data was exposed, how many people in which states, every source URL.
2. **A triage score** (0–100, with one-line reasons): is this worth an attorney's hour today?
3. **A draft class-action complaint** for an attorney to edit and file. Every factual allegation quotes the defendant's own notice and links it. Every statute comes from a table checked against the official text.

The tool is for licensed counsel. It never files anything, never contacts class members and never advises consumers. Every draft opens with **"Draft for review by licensed counsel. Not legal advice."**

## Why speed

Data-breach class actions went from 604 filings (2022) to 1,488 (2024) to 1,822 (2025) (Duane Morris Class Action Review). Holland & Knight ("Race to the Courthouse", 2026-10-23) describes plaintiff firms filing within hours or days of a breach going public, working from SEC 8-K Item 1.05 filings and state AG breach notices. Filing first helps win interim lead counsel. Raw breach rows are already cheap on Apify. This actor sells the next two steps, triage and the draft.

## Sources

| source | what it gives |
|---|---|
| `sec_8k` | EDGAR full-text search for 8-Ks citing Item 1.05 or a cybersecurity incident. Includes the Item text and the company's CIK, ticker, state of incorporation and business address. |
| `ca_ag` | California AG breach list plus each sample consumer letter (PDF), which says what happened, when and what data was involved. A filing here means more than 500 California residents were affected. |
| `wa_ag` | Washington AG notices from data.wa.gov: the Washington resident count, breach and discovery dates, cause, data types and days to notify. |

Notices from different sources that name the same organisation are merged into one incident.

## How the draft stays honest

- **Facts.** Claude (`claude-opus-5-5`) writes the factual allegations. Each one must carry a verbatim quote from a source notice, and code keeps it only if that quote is actually in the notice text. Any model sentence that names a statute, an Act or a case is dropped. Allegations from structured fields (such as the WA resident count) are written by code, not the model.
- **Law.** Every section cited comes from `data/statutes.json`. `scripts/verify_statutes.mjs` builds that table by opening each official page (govinfo.gov, eCFR, leginfo.legislature.ca.gov, app.leg.wa.gov) and checking that the required text is present. It records the URL, the date and the quote. On 2026-11-03 it caught an amendment: Cal. Civ. Code § 1798.82 now requires notice "within 30 calendar days". A state outside the table prints `[state statutory counts: attorney to add]`.
- **Audit.** After assembly, every citation-shaped string in the draft is checked against the table (`citation_audit_unverified` in the output; empty on every example).

Verified today (13 sections): 28 U.S.C. § 1332(d) (CAFA), 15 U.S.C. § 45(a) (FTC Act § 5), 45 C.F.R. §§ 164.306 and 164.404 (HIPAA), Cal. Civ. Code §§ 1798.150, 1798.81.5, 1798.82, 1798.84, Cal. Bus. & Prof. Code § 17200, and RCW 19.255.010, 19.255.040, 19.86.020 and 19.86.090.

Counts drafted: negligence, negligence per se (FTC Act; HIPAA for healthcare defendants), breach of implied contract, unjust enrichment, breach of fiduciary duty (healthcare and finance only), California statutory counts (CCPA statutory damages only when the letter names CCPA-qualifying data), Washington statutory counts, and a placeholder for other states.

## Pricing (pay per event)

| event | price | why |
|---|---|---|
| `incident` | $0.01 | Raw breach rows are a commodity. Several Apify actors already scrape AG lists, so this has to cost almost nothing. |
| `triage` | $0.50 | Scores sit between 18 and 74 on a three-week sample (5 file, 7 watch, 3 pass). That lets a firm skip most incidents without reading them, which is worth more than a row and still costs less than a minute of paralegal time. |
| `complaint-draft` | $19.00 | Claude costs per draft were measured at $0.035–$0.069 across four examples (989–6,760 input tokens, 1,568–2,512 output, at $4/$20 per million). Apify pays out 80% minus platform costs, so $19 leaves about $15 per draft, more than 200 times the model cost. Drafting a complaint by hand takes an associate hours. |

Users can pass their own `anthropicApiKey`. The price stays the same, because what the draft sells is the pipeline and the verified table, not the tokens.

## Run it

Locally:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # without it, drafts are template-only
.venv/bin/python run_local.py --days 3                          # everything from the last 3 days
.venv/bin/python run_local.py --days 21 --only "Park Dental" --min-score 0
```

On Apify, the input is `sources`, `lookbackDays`, `maxNoticesPerSource`, `onlyNames`, `draftComplaints`, `minScoreForDraft` and `anthropicApiKey`. Schedule it hourly with `lookbackDays: 1`. Incidents go to the dataset. Each draft is a Markdown record in the key-value store, linked from `draft.markdown_url`.

Re-verify the statute table (headless Chromium via Playwright):

```bash
PLAYWRIGHT_DIR=/path/to/node_modules node scripts/verify_statutes.mjs
```

## Examples (real breaches, run 2026-11-03)

| incident | source | score | draft |
|---|---|---|---|
| DriveWealth, LLC (358,104 WA residents, SSNs) | WA AG | 74 | [`examples/drivewealth-20261013.complaint.md`](examples/drivewealth-20261013.complaint.md) |
| University of Mississippi Medical Center | CA AG letter | 46 (watch) | none: a state institution, so the government-defendant check drops it below the draft threshold |
| EyeMed Vision Care, LLC | CA AG letter | 67 | [`examples/eyemed-vision-care-20261030.complaint.md`](examples/eyemed-vision-care-20261030.complaint.md) |
| Park Dental Partners, Inc. (PARK) | SEC 8-K Item 1.05 | 52 | [`examples/park-dental-partners-20261013.complaint.md`](examples/park-dental-partners-20261013.complaint.md) |

## What it does not know yet

From `DATA_MODEL.md` (the model was written before looking at any source, then checked field by field):

- **Total class size.** None of the three sources states a national count. Maine AG and HHS OCR do, and they are the next sources to add.
- **Whether someone has already filed.** CourtListener's API needs a token, and PACER costs money. The draft tells counsel to check the dockets.
- Prior breaches by the same defendant, leak-site publication, minors in the class, and encryption.

## Status

- Pipeline, triage, drafting, the verified table and four real examples: done.
- The actor runs locally under the Apify SDK with test pay-per-event charging. Events fire in order: incident, triage, complaint-draft.
- **Monetized 2026-11-03** on Apify (`cheerful_term/breach-to-complaint`): pay-per-event pricing above is live; the console shows Monetization: Active. The Actor is still **private**: `scripts/publish.sh` (token now in `~/.config/breach-to-complaint/.env`) makes it public in the Store.

## Licence

MIT. Statute text belongs to the public domain or its respective governments. The repo holds only short verification quotes and links.
