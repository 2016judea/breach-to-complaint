"""Shared pipeline: notices -> incidents -> triage -> drafts. Used by the actor and the CLI."""
from __future__ import annotations

import datetime as dt
import logging

import draft as D
import incident as I
import sources as S

logging.getLogger("pypdf").setLevel(logging.ERROR)


def public_incident(inc: dict, tri: dict) -> dict:
    out = {k: v for k, v in inc.items() if not k.startswith("_")}
    out["triage"] = tri
    return out


def run(cfg: dict, on_incident=None, on_triage=None, on_draft=None) -> list[dict]:
    """cfg: sources, lookbackDays, maxNoticesPerSource, minScoreForDraft, draftComplaints, anthropicApiKey, onlyNames.

    The on_* callbacks return False to stop (spending limit reached)."""
    today = dt.date.fromisoformat(cfg["today"]) if cfg.get("today") else dt.date.today()
    since = today - dt.timedelta(days=int(cfg.get("lookbackDays", 3)))
    notices = S.fetch(cfg.get("sources", ["sec_8k", "ca_ag", "wa_ag"]), since, today, int(cfg.get("maxNoticesPerSource", 25)))
    errors = [n for n in notices if n.get("error")]
    incidents = I.merge(notices)
    only = [I.norm_name(x) for x in cfg.get("onlyNames") or []]
    if only:
        incidents = [x for x in incidents if any(o in I.norm_name(x["defendant"]["legal_name"]) for o in only)]
    results = []
    for inc in incidents:
        inc["_generated_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        tri = I.triage(inc, today)
        rec = public_incident(inc, tri)
        if on_incident and on_incident(rec) is False:
            break
        if cfg.get("triage", True):
            if on_triage and on_triage(rec) is False:
                rec["triage"] = {"score": None, "note": "spending limit reached before triage"}
        want = cfg.get("draftComplaints", True) and tri["score"] is not None and tri["score"] >= int(cfg.get("minScoreForDraft", 55))
        if want:
            allegations, usage = D.draft_allegations(inc, cfg.get("anthropicApiKey"))
            allegations = D.structured_allegations(inc) + allegations
            comp = D.build_complaint(inc, tri, allegations)
            comp["llm"] = usage
            comp["citation_audit_unverified"] = D.audit_citations(comp["markdown"])
            rec["draft"] = {k: v for k, v in comp.items() if k != "markdown"}
            rec["draft_markdown"] = comp["markdown"]
            if on_draft and on_draft(rec) is False:
                rec.pop("draft_markdown", None)
                rec["draft"] = {"note": "spending limit reached before draft"}
        results.append(rec)
    results.sort(key=lambda r: -(r["triage"].get("score") or 0))
    if errors:
        results.append({"source_errors": errors})
    return results
