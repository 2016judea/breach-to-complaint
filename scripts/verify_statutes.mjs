// Verify every statute in data/statutes_source.json against the legislature's
// (or eCFR's / the House OLRC's) own page, and write data/statutes.json with
// only the entries whose required text was found on that page today.
//
// The actor never cites law from anywhere else. A statute that fails here is
// dropped from the table, and a draft that would have used it says
// "[state statutory counts: attorney to add]" instead.
//
// Run: PLAYWRIGHT_DIR=~/Desktop/pricing-vintage-baseball-cards/node_modules node scripts/verify_statutes.mjs
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const require = createRequire(path.join(process.env.PLAYWRIGHT_DIR || path.join(root, "node_modules"), "x.js"));
const { chromium } = require("playwright");

const norm = (s) => s.replace(/ /g, " ").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, " ");
const today = new Date().toISOString().slice(0, 10);

async function ecfrDate() {
  const r = await fetch("https://www.ecfr.gov/api/versioner/v1/titles", { headers: { "accept-encoding": "gzip" } });
  const t = (await r.json()).titles.find((x) => x.number === 45);
  return t.up_to_date_as_of;
}

const src = JSON.parse(fs.readFileSync(path.join(root, "data/statutes_source.json"), "utf8"));
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36" });
const page = await ctx.newPage();
const eDate = await ecfrDate();
const out = [];
const failed = [];

for (const s of src) {
  const url = s.url.replace("{ecfr_date}", eDate);
  let text = "";
  try {
    if (url.includes("ecfr.gov/api")) {
      const r = await fetch(url, { headers: { "accept-encoding": "gzip" } });
      text = (await r.text()).replace(/<[^>]+>/g, " ");
    } else {
      await page.waitForTimeout(2500); // be polite; leginfo's bot wall trips on bursts
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
      for (let i = 0; i < 20; i++) {
        if (i === 10) await page.reload({ waitUntil: "domcontentloaded" });
        text = await page.evaluate(() => document.body.innerText);
        if (!/Just a moment|Checking your browser|security verification/i.test(text) && text.length > 800) break;
        await page.waitForTimeout(1500);
      }
    }
  } catch (e) {
    failed.push({ id: s.id, url, reason: String(e).slice(0, 120) });
    continue;
  }
  const t = norm(text);
  const missing = s.must_contain.filter((p) => !t.includes(norm(p)));
  if (missing.length) {
    failed.push({ id: s.id, url, reason: `required text not found: ${missing.join(" | ")}`, chars: t.length });
    continue;
  }
  const p0 = norm(s.must_contain[0]);
  const i = t.indexOf(p0);
  out.push({
    id: s.id, cite: s.cite, title: s.title, jurisdiction: s.jurisdiction, use: s.use,
    ...(s.industry ? { industry: s.industry } : {}),
    url: s.human_url || url,
    verified_on: today,
    verified_quote: t.slice(Math.max(0, i - 80), i + p0.length + 120).trim(),
    page_sha256: crypto.createHash("sha256").update(t).digest("hex"),
  });
  console.log("ok   ", s.id);
}
await browser.close();
for (const f of failed) console.log("FAIL ", f.id, f.reason);
fs.writeFileSync(path.join(root, "data/statutes.json"), JSON.stringify({ verified_on: today, method: "scripts/verify_statutes.mjs", statutes: out, failed }, null, 1) + "\n");
console.log(`${out.length} verified, ${failed.length} failed -> data/statutes.json`);
