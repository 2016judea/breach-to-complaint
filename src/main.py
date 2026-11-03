"""Apify actor entry point. Pay-per-event: incident, triage, complaint-draft."""
from __future__ import annotations

import asyncio
import os
import re

from apify import Actor

import pipeline


async def main() -> None:
    async with Actor:
        cfg = await Actor.get_input() or {}
        # The draft step runs on the publisher's Anthropic key (an actor env secret)
        # unless the user brings their own.
        if not cfg.get("anthropicApiKey"):
            cfg["anthropicApiKey"] = os.environ.get("ANTHROPIC_API_KEY")
        loop = asyncio.get_running_loop()
        store = await Actor.open_key_value_store()

        def charge(event: str):
            def cb(rec):
                fut = asyncio.run_coroutine_threadsafe(Actor.charge(event_name=event), loop)
                return not fut.result().event_charge_limit_reached
            return cb

        def on_draft(rec):
            key = re.sub(r"[^A-Za-z0-9!\-_.'()]", "-", rec["incident_id"])[:200] + ".md"
            asyncio.run_coroutine_threadsafe(store.set_value(key, rec["draft_markdown"], content_type="text/markdown"), loop).result()
            rec["draft"]["markdown_key"] = key
            rec["draft"]["markdown_url"] = f"https://api.apify.com/v2/key-value-stores/{store.id}/records/{key}"
            asyncio.run_coroutine_threadsafe(Actor.charge(event_name="complaint-draft"), loop).result()
            return True

        results = await asyncio.to_thread(
            pipeline.run, cfg,
            on_incident=charge("incident"),
            on_triage=charge("triage"),
            on_draft=on_draft,
        )
        for r in results:
            if r.get("draft", {}).get("markdown_key"):
                r.pop("draft_markdown", None)
            await Actor.push_data(r)
        Actor.log.info(f"{sum(1 for r in results if 'incident_id' in r)} incidents, {sum(1 for r in results if r.get('draft', {}).get('markdown_key'))} drafts")


if __name__ == "__main__":
    asyncio.run(main())
