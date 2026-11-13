#!/usr/bin/env python3
"""Render an invoice PDF from JSON + config/invoice.json. Stdlib only (runs on any python3).

usage: render_invoice.py INVOICE.json [--no-pdf]
Writes ~/.hermes/jarvis-invoices/<number>.html and .pdf, appends to ledger.json,
prints {"number", "pdf", "html", "total"} as JSON.
"""

from __future__ import annotations

import glob
import html
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
OUT_DIR = HERMES_HOME / "jarvis-invoices"
TEMPLATE = Path(__file__).with_name("template.html")


def _repo_root() -> Path:
    marker = HERMES_HOME / "plugins" / "jarvis-guard" / ".repo_root"
    if marker.exists():
        return Path(marker.read_text().strip())
    return Path(__file__).resolve().parents[3]


def _load_config(path: Path) -> dict:
    return json.loads(path.read_text()) or {}


def _chromium() -> str | None:
    pats = [str(HERMES_HOME / "tools" / "chromium-*" / "chrome-mac-arm64" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for pat in pats:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def next_number(ledger: list[dict], year: int) -> str:
    n = sum(1 for e in ledger if e.get("number", "").startswith(f"INV-{year}-")) + 1
    return f"INV-{year}-{n:04d}"


def money(x: float) -> str:
    return f"${x:,.2f}"


def render(data: dict, cfg: dict, number: str, today: date) -> tuple[str, float]:
    biz_key = data.get("business", "")
    biz = (cfg.get("businesses") or {}).get(biz_key)
    if not biz:
        raise SystemExit(f"unknown business '{biz_key}'; add it to config/invoice.json")
    client = data.get("client") or {}
    items = data.get("items") or []
    if not items:
        raise SystemExit("no line items")
    rows, subtotal = [], 0.0
    for it in items:
        qty = float(it.get("qty", 1)); price = float(it["unit_price"]); line = qty * price
        subtotal += line
        rows.append(f"<tr><td>{html.escape(str(it['description']))}</td><td class=n>{qty:g}</td>"
                    f"<td class=n>{money(price)}</td><td class=n>{money(line)}</td></tr>")
    tax_rate = float(data.get("tax_rate", biz.get("tax_rate", 0)) or 0)
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    due = today + timedelta(days=int(data.get("due_days", cfg.get("default_due_days", 14))))
    payment = biz.get("payment") or {}
    pay_lines = "<br>".join(html.escape(f"{k.replace('_', ' ').title()}: {v}") for k, v in payment.items() if v)
    if not payment.get("details"):
        raise SystemExit(f"no payment details for '{biz_key}' in config/invoice.json; Ben must fill them in")
    tpl = TEMPLATE.read_text()
    out = (tpl.replace("{{BUSINESS_NAME}}", html.escape(biz.get("name", biz_key)))
              .replace("{{BUSINESS_LINES}}", "<br>".join(html.escape(l) for l in biz.get("address_lines", []) + [biz.get("email", "")] if l))
              .replace("{{NUMBER}}", number)
              .replace("{{DATE}}", today.isoformat())
              .replace("{{DUE}}", due.isoformat())
              .replace("{{CLIENT_NAME}}", html.escape(client.get("name", "")))
              .replace("{{CLIENT_LINES}}", "<br>".join(html.escape(l) for l in [client.get("email", "")] + list(client.get("address_lines", [])) if l))
              .replace("{{ROWS}}", "\n".join(rows))
              .replace("{{SUBTOTAL}}", money(subtotal))
              .replace("{{TAX_ROW}}", f"<tr><td colspan=3 class=n>Tax ({tax_rate:.1%})</td><td class=n>{money(tax)}</td></tr>" if tax else "")
              .replace("{{TOTAL}}", money(total))
              .replace("{{PAYMENT}}", pay_lines)
              .replace("{{NOTES}}", html.escape(data.get("notes", ""))))
    return out, total


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__); return 2
    data = json.loads(Path(argv[1]).read_text())
    cfg = _load_config(Path(os.environ.get("JARVIS_INVOICE_CONFIG") or _repo_root() / "config" / "invoice.json"))
    OUT_DIR = Path(os.environ.get("JARVIS_INVOICE_DIR") or globals()["OUT_DIR"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = OUT_DIR / "ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    today = date.today()
    number = data.get("number") or next_number(ledger, today.year)
    html_out, total = render(data, cfg, number, today)
    html_path = OUT_DIR / f"{number}.html"
    html_path.write_text(html_out)
    pdf_path = OUT_DIR / f"{number}.pdf"
    pdf_ok = False
    if "--no-pdf" not in argv:
        chrome = _chromium()
        if chrome:
            subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                            f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            pdf_ok = pdf_path.exists() and pdf_path.stat().st_size > 500
    ledger.append({"number": number, "date": today.isoformat(), "business": data.get("business"),
                   "client": (data.get("client") or {}).get("name"), "total": total, "pdf": str(pdf_path) if pdf_ok else None,
                   "status": "drafted"})
    ledger_path.write_text(json.dumps(ledger, indent=2))
    print(json.dumps({"number": number, "total": total, "html": str(html_path), "pdf": str(pdf_path) if pdf_ok else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
