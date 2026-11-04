#!/usr/bin/env bash
# Push the actor to Apify, set its Anthropic secret, apply pay-per-event pricing and
# make it public in the Store. Needs APIFY_TOKEN in ~/.config/breach-to-complaint/.env
# (Apify Console -> Settings -> API & Integrations). Spends nothing: Apify's free and
# Creator plans can publish; users pay per event, Apify pays out 80% minus platform costs.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source ~/.config/breach-to-complaint/.env; set +a
: "${APIFY_TOKEN:?add APIFY_TOKEN=apify_api_... to ~/.config/breach-to-complaint/.env}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$(grep '^ANTHROPIC_API_KEY=' ~/Desktop/bricks/.env | cut -d= -f2- | tr -d '"')}"
CLI="npx -y apify-cli@1.10.0"

$CLI login -t "$APIFY_TOKEN" >/dev/null
$CLI secrets rm anthropicApiKey >/dev/null 2>&1 || true
$CLI secrets add anthropicApiKey "$ANTHROPIC_API_KEY"
$CLI push

USER=$(curl -s "https://api.apify.com/v2/users/me?token=$APIFY_TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['username'])")
ACT="$USER~breach-to-complaint"
python3 - "$ACT" "$APIFY_TOKEN" <<'PY'
import json, sys, urllib.request
act, token = sys.argv[1], sys.argv[2]
ppe = json.load(open(".actor/pay_per_event.json"))
cur = json.load(urllib.request.urlopen(f"https://api.apify.com/v2/acts/{act}?token={token}"))["data"]
body = {
  "isPublic": True,
  "title": "Data Breach to Class Action Complaint Draft",
  "categories": ["LEAD_GENERATION", "AI"],
}
# Apify refuses frequent pricing edits; only send pricing when it changed.
live = (cur.get("pricingInfos") or [{}])[-1].get("pricingPerEvent")
if live != ppe["pricingPerEvent"]:
    body["pricingInfos"] = [ppe]
req = urllib.request.Request(f"https://api.apify.com/v2/acts/{act}?token={token}", data=json.dumps(body).encode(),
                             method="PUT", headers={"content-type": "application/json"})
r = json.load(urllib.request.urlopen(req))["data"]
print("public:", r.get("isPublic"), "pricing:", [p.get("pricingModel") for p in r.get("pricingInfos", [])])
print("Store URL: https://apify.com/" + act.replace("~", "/"))
PY
