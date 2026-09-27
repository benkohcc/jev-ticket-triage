#!/usr/bin/env python3
"""Run the synthetic support tickets through Jev and write report.html.

Live:     put TYPESAFE_API_KEY=... in .env (see .env.example), then python3 triage.py
Preview:  python3 triage.py --mock      (fake answers, report is marked SIMULATED)

Standard library only - no pip install needed.
"""
import argparse
import datetime as dt
import json
import os
import random
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
PRICE_PER_INPUT_TOKEN = 42 / 1e9  # $42 per billion input tokens; output tokens are free
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}

TEAMS = {
    "billing": "Charges, refunds, invoices, payment methods, plan or price changes, tax and payment terms",
    "bug": "Something in the product is broken, erroring, crashing, down, or behaving incorrectly",
    "account": "Login, passwords, 2FA, SSO, users and permissions, workspace settings, account closure or data deletion",
    "feature_request": "Asking for new functionality or an improvement that does not exist yet",
    "spam": "Unsolicited marketing, scams, phishing, auto-replies, or messages that are not from a real customer",
}

URGENCY_LEVELS = [
    "1 - Low: general question or suggestion, no time pressure",
    "2 - Minor: small inconvenience, can wait a few days",
    "3 - Moderate: real problem for one user or money in dispute, should be handled today",
    "4 - High: important workflow blocked or customer escalating",
    "5 - Critical: outage, data loss, security incident, or whole team blocked right now",
]

QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which support team should handle this ticket?",
        "criteria": TEAMS,
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this support ticket?",
        "criteria": URGENCY_LEVELS,
    },
    "churn_risk": {
        "type": "noul",
        "instructions": "Is the customer threatening or signalling that they will cancel, leave, or switch to a competitor?",
    },
}


def load_dotenv(path=HERE / ".env"):
    """Load KEY=value lines from .env; variables already set in the shell win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def call_jev(text, api_key, attempts=5):
    body = json.dumps({"state": text, "model": MODEL, "questions": QUESTIONS}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    for attempt in range(attempts):
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            data["latency_ms"] = (time.perf_counter() - start) * 1000
            return data
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUSES or attempt == attempts - 1:
                raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')}") from e
        except urllib.error.URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(0.5 * 2**attempt + random.random() * 0.25)


def mock_jev(ticket, rng):
    """Plausible fake answers so the report can be previewed without an API key."""
    teams = list(TEAMS)
    right = rng.random() < 0.88
    pick = ticket["team"] if right else rng.choice([t for t in teams if t != ticket["team"]])
    top = rng.uniform(0.55, 0.99) if right else rng.uniform(0.35, 0.7)
    rest = [rng.random() for _ in teams[1:]]
    probs = {pick: top}
    for t, r in zip([t for t in teams if t != pick], rest):
        probs[t] = (1 - top) * r / sum(rest)

    level = min(4, max(0, ticket["urgency"] - 1 + rng.choice([-1, 0, 0, 0, 1])))
    raw = [0.02 + (0.9 if i == level else 0.25 * rng.random() / (1 + abs(i - level))) for i in range(5)]
    uprobs = {str(i): p / sum(raw) for i, p in enumerate(raw)}
    noul = rng.uniform(0.7, 0.98) if ticket["churn"] else rng.uniform(0.01, 0.3)
    if rng.random() < 0.06:
        noul = 1 - noul

    return {
        "model": "SIMULATED",
        "answers": {
            "team": {"type": "choice", "choice": pick, "probabilities": probs,
                     "confidence": round(top - sorted(probs.values())[-2], 3)},
            "urgency": {"type": "score", "score": sum(i * p for i, p in enumerate(uprobs.values())),
                        "legend": {str(i): l for i, l in enumerate(URGENCY_LEVELS)},
                        "probabilities": uprobs, "confidence": round(max(uprobs.values()), 3)},
            "churn_risk": {"type": "noul", "noul": noul},
        },
        "usage": {"input_tokens": 280 + len(ticket["text"]) // 4, "output_tokens": 60},
        "latency_ms": rng.uniform(80, 180),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="use simulated answers instead of calling Jev")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests (default 8)")
    ap.add_argument("--limit", type=int, help="only run the first N tickets")
    args = ap.parse_args()

    tickets = json.loads((HERE / "tickets.json").read_text())[: args.limit]
    load_dotenv()
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not args.mock and not api_key:
        raise SystemExit("Set TYPESAFE_API_KEY in .env or your shell (key from https://console.typesafe.ai/keys), or pass --mock.")

    rng = random.Random(7)
    run = (lambda t: mock_jev(t, rng)) if args.mock else (lambda t: call_jev(t["text"], api_key))

    def one(t):
        try:
            return {**t, "jev": run(t)}
        except Exception as e:  # keep going; the report shows failed tickets
            return {**t, "error": str(e)}

    wall = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1 if args.mock else args.workers) as pool:
        results = list(pool.map(one, tickets))
    wall = time.perf_counter() - wall

    ok = [r for r in results if "jev" in r]
    input_tokens = sum(r["jev"]["usage"]["input_tokens"] for r in ok)
    meta = {
        "mode": "mock" if args.mock else "live",
        "model": ok[0]["jev"]["model"] if ok else MODEL,
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
        "wall_seconds": wall,
        "input_tokens": input_tokens,
        "cost_usd": input_tokens * PRICE_PER_INPUT_TOKEN,
        "teams": TEAMS,
        "urgency_levels": URGENCY_LEVELS,
    }
    payload = {"meta": meta, "results": results}

    (HERE / "results.json").write_text(json.dumps(payload, indent=2))
    template = (HERE / "report_template.html").read_text()
    data = json.dumps(payload).replace("</", "<\\/")
    (HERE / "report.html").write_text(template.replace("/*__DATA__*/null", data))

    correct = sum(r["jev"]["answers"]["team"]["choice"] == r["team"] for r in ok)
    print(f"{meta['mode'].upper()} run: {len(ok)}/{len(results)} tickets answered in {wall:.1f}s")
    if ok:
        lat = statistics.median(r["jev"]["latency_ms"] for r in ok)
        print(f"team accuracy {correct}/{len(ok)} | median latency {lat:.0f} ms | "
              f"{input_tokens:,} input tokens = ${meta['cost_usd']:.6f}")
    for r in results:
        if "error" in r:
            print(f"  {r['id']} failed: {r['error'][:200]}")
    print(f"Report: {HERE / 'report.html'}")


if __name__ == "__main__":
    main()
