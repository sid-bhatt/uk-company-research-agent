"""Smoke test against the deployed API: search like the web app (first hit, as pressing
Enter does), run a report, follow its progress events, and check the result.

Signs a short-lived token with API_JWT_SECRET from SSM, exactly as the web app does.
Prints a Markdown table (for docs/smoke-test-*.md). Uses the daily report quota.

    AWS_PROFILE=aws-new uv run python scripts/smoke_live.py https://<api-host> greggs 13235004 …
"""

import json
import subprocess
import sys
import time

import httpx
import jwt


def token(region: str = "eu-north-1") -> str:
    secret = subprocess.run(
        ["aws", "ssm", "get-parameter", "--region", region, "--name", "/ukco/API_JWT_SECRET",
         "--with-decryption", "--query", "Parameter.Value", "--output", "text"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    now = int(time.time())
    claims = {"sub": "github:smoke-test", "iss": "ukco-web", "aud": "ukco-api"}
    return jwt.encode({**claims, "iat": now, "exp": now + 900}, secret, algorithm="HS256")


def run(http: httpx.Client, query: str) -> dict[str, object]:
    number = query
    if not query.isdigit():
        hits = http.get("/companies/search", params={"q": query}).raise_for_status().json()
        number = hits[0]["company_number"]
    started = time.perf_counter()
    created = http.post("/reports", json={"company_number": number}).raise_for_status().json()
    stages = []
    with http.stream("GET", f"/reports/{created['id']}/events") as events:
        for line in events.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                stages.append(event.get("stage", event["event"]))
    seconds = time.perf_counter() - started
    detail = http.get(f"/reports/{created['id']}").raise_for_status().json()
    report = detail.get("report") or {}
    company = report.get("company") or {}
    checks = report.get("checks") or []
    return {
        "query": query,
        "number": number,
        "name": company.get("name", "?"),
        "company_status": company.get("status", "?"),
        "status": detail["status"],
        "seconds": round(seconds, 1),
        "score": detail.get("risk_score"),
        "flags": [f"{f['flag_type']} ({f['severity']})" for f in report.get("flags", [])],
        "not_assessed": [c["rule"] for c in checks if c["status"] == "not_assessed"],
        "evidence": len(report.get("evidence", [])),
        "ch_requests": (report.get("provenance") or {}).get("companies_house_requests"),
        "stages_ok": stages[-1:] == ["succeeded"],
        "error": detail.get("error"),
    }


def main() -> None:
    base, queries = sys.argv[1], sys.argv[2:]
    rows = []
    with httpx.Client(
        base_url=base, headers={"Authorization": f"Bearer {token()}"}, timeout=300
    ) as http:
        for q in queries:
            try:
                rows.append(run(http, q))
            except httpx.HTTPError as exc:
                rows.append({"query": q, "status": "error", "error": str(exc)})
    columns = ["Searched", "Company", "Status", "Report", "Time", "Score", "Flags",
               "Not assessed", "Evidence", "CH calls"]  # fmt: skip
    print("| " + " | ".join(columns) + " |")
    print("|" + "---|" * len(columns))
    for r in rows:
        print(
            f"| `{r['query']}` | {r.get('name', '?')} ({r.get('number', '?')}) | "
            f"{r.get('company_status', '?')} | {r['status']} | {r.get('seconds', '?')} s | "
            f"{r.get('score', '?')} | {', '.join(r.get('flags', [])) or 'none'} | "  # type: ignore[arg-type]
            f"{', '.join(r.get('not_assessed', [])) or 'none'} | {r.get('evidence', '?')} | "  # type: ignore[arg-type]
            f"{r.get('ch_requests', '?')} |"
        )
    failed = [r for r in rows if r["status"] != "succeeded" or not r.get("stages_ok")]
    for r in failed:
        print(f"\nFAILED {r['query']}: {r.get('error')}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
