---
name: jarvis-invoice
description: "Draft an invoice PDF from Ben's template, email it after approval. Numbering and PDF rendering handled by scripts/render_invoice.py."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [invoice, billing, pdf, jarvis]
    related_skills: [jarvis-google, jarvis-reply-email]
---

# Jarvis: send an invoice

Jarvis prepares invoices; it never collects or moves money. Payment links/details come from `config/invoice.json` in the JARVIS repo (Ben maintains it).

```bash
GAPI="${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-google/scripts/gapi"
RENDER="${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-invoice/scripts/render_invoice.py"
```

## Procedure
1. **Collect the facts** from Ben (ask once, in one message, for anything missing): which business is billing (`dailybuilt`, `oceancrest`, or `personal` from config), client name + email, line items (description, quantity, unit price), due date (default: 14 days), optional notes.
2. **Write the invoice JSON** to a temp file, e.g. `/tmp/invoice.json`:
   ```json
   {"business": "dailybuilt", "client": {"name": "Acme LLC", "email": "ap@acme.com"},
    "items": [{"description": "Dashboard setup", "qty": 1, "unit_price": 500.00}],
    "due_days": 14, "notes": "Thanks for your business."}
   ```
3. **Render** (assigns the next invoice number, writes HTML + PDF):
   ```bash
   python3 "$RENDER" /tmp/invoice.json
   ```
   Prints JSON with `number`, `pdf`, `total`. Rendering is Tier 1 (nothing leaves the Mac).
4. **Copy the PDF to `~/Downloads/Jarvis/`** (`mkdir -p ~/Downloads/Jarvis && cp <pdf> ~/Downloads/Jarvis/`) and **show Ben** the number, total, due date, and the `Downloads/Jarvis/...` path. Draft the cover email (three versions, confidence-ranked, professional tone since it's a client).
5. **Send** when Ben has told you to; otherwise show him the draft and ask who it goes to.
   ```bash
   $GAPI gmail send --to ap@acme.com --subject "Invoice INV-2026-0003 from DailyBuilt" --body "..." --attach /Users/you/.hermes/jarvis-invoices/INV-2026-0003.pdf
   ```
6. **Confirm** and remind Ben the invoice is logged at `~/.hermes/jarvis-invoices/ledger.json`.

## Never
- Invent prices, bank details, or payment links. If the renderer says payment details are missing, tell Ben to fill `config/invoice.json` and stop.
- Send to an address Ben didn't name.
