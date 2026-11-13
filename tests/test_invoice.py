import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "invoice" / "scripts" / "render_invoice.py"

spec = importlib.util.spec_from_file_location("render_invoice", SCRIPT)
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)

CFG = {"default_due_days": 14, "businesses": {"dailybuilt": {
    "name": "DailyBuilt", "email": "billing@dailybuilt.test", "address_lines": ["1 Main St"],
    "tax_rate": 0, "payment": {"method": "Zelle", "details": "pay@dailybuilt.test", "terms": "Net 14"}}}}


def test_numbering_per_year():
    assert ri.next_number([], 2026) == "INV-2026-0001"
    assert ri.next_number([{"number": "INV-2026-0001"}, {"number": "INV-2025-0009"}], 2026) == "INV-2026-0002"


def test_render_totals_and_fields():
    data = {"business": "dailybuilt", "client": {"name": "Acme", "email": "ap@acme.test"},
            "items": [{"description": "Setup <fee>", "qty": 2, "unit_price": 150.5}, {"description": "Hosting", "qty": 1, "unit_price": 49}],
            "due_days": 10, "notes": "Thanks"}
    html, total = ri.render(data, CFG, "INV-2026-0007", date(2026, 10, 9))
    assert total == 350.0
    assert "INV-2026-0007" in html and "2026-10-19" in html and "$350.00" in html
    assert "Setup &lt;fee&gt;" in html  # escaped
    assert "pay@dailybuilt.test" in html


def test_refuses_without_payment_details():
    cfg = {"businesses": {"x": {"name": "X", "payment": {"method": "", "details": ""}}}}
    try:
        ri.render({"business": "x", "client": {}, "items": [{"description": "a", "qty": 1, "unit_price": 1}]}, cfg, "INV-1", date.today())
        assert False, "should have refused"
    except SystemExit as e:
        assert "payment" in str(e)


def test_cli_writes_html_and_ledger(tmp_path):
    cfg_path = tmp_path / "invoice.json"
    cfg_path.write_text(json.dumps(CFG))
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"business": "dailybuilt", "client": {"name": "Acme"}, "items": [{"description": "a", "qty": 1, "unit_price": 10}]}))
    out_dir = tmp_path / "out"
    r = subprocess.run([sys.executable, str(SCRIPT), str(inv), "--no-pdf"], capture_output=True, text=True,
                       env={"JARVIS_INVOICE_CONFIG": str(cfg_path), "JARVIS_INVOICE_DIR": str(out_dir), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    assert res["number"].startswith("INV-") and res["total"] == 10.0
    assert (out_dir / f"{res['number']}.html").exists()
    assert json.loads((out_dir / "ledger.json").read_text())[0]["status"] == "drafted"
