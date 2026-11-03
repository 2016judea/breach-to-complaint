"""Run the pipeline locally and write examples/: python run_local.py --today 2026-11-03 --days 21 --only "Park Dental" ..."""
import argparse, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
import pipeline

ap = argparse.ArgumentParser()
ap.add_argument("--today"); ap.add_argument("--days", type=int, default=3)
ap.add_argument("--sources", default="sec_8k,ca_ag,wa_ag"); ap.add_argument("--max", type=int, default=25)
ap.add_argument("--only", nargs="*"); ap.add_argument("--min-score", type=int, default=55)
ap.add_argument("--out", default="examples"); ap.add_argument("--no-llm", action="store_true")
a = ap.parse_args()
key = None if a.no_llm else os.environ.get("ANTHROPIC_API_KEY")
res = pipeline.run({"today": a.today, "lookbackDays": a.days, "sources": a.sources.split(","), "maxNoticesPerSource": a.max,
                    "onlyNames": a.only, "minScoreForDraft": a.min_score, "anthropicApiKey": key})
out = Path(a.out); out.mkdir(exist_ok=True)
for r in res:
    if "incident_id" not in r:
        print("source errors:", r); continue
    slug = re.sub(r"[^a-z0-9-]", "", r["incident_id"])[:70]
    md = r.pop("draft_markdown", None)
    (out / f"{slug}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    if md: (out / f"{slug}.complaint.md").write_text(md)
    llm = (r.get("draft") or {}).get("llm", {})
    print(f"{r['triage']['score']:>3} {r['triage']['band']:<5} {r['defendant']['legal_name'][:45]:<45} {','.join(n['source'] for n in r['notices'])}  draft={'yes' if md else 'no'} kept={llm.get('allegations_kept')} dropped={len(llm.get('allegations_dropped', []))} tok={llm.get('input_tokens')}/{llm.get('output_tokens')} audit={(r.get('draft') or {}).get('citation_audit_unverified')}")
