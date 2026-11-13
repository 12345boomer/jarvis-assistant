#!/usr/bin/env python3
"""deck_check.py DECK.pptx [--out DIR]
Visual QA: renders the deck with LibreOffice to PDF, then to PNGs (one per slide) plus a contact sheet
`contact.png`, and prints slide count, text-heavy slides, and the image paths so the agent can LOOK at them
(vision) before delivering. Also runs a quick sanity check that every slide has a background and under ~80 words."""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SOFFICE = [p for p in ["/Applications/LibreOffice.app/Contents/MacOS/soffice", "/opt/homebrew/bin/soffice", "/usr/local/bin/soffice"] if Path(p).exists()]


def slide_stats(pptx: Path) -> list[dict]:
    out = []
    with zipfile.ZipFile(pptx) as z:
        names = sorted([n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)], key=lambda n: int(re.findall(r"\d+", n)[0]))
        for n in names:
            xml = z.read(n).decode("utf-8", "replace")
            words = len(re.findall(r"<a:t>([^<]*)</a:t>", xml)) and sum(len(t.split()) for t in re.findall(r"<a:t>([^<]*)</a:t>", xml))
            rel = z.read(n.replace("slides/", "slides/_rels/") + ".rels").decode("utf-8", "replace") if n.replace("slides/", "slides/_rels/") + ".rels" in z.namelist() else ""
            out.append({"slide": n.split("slide")[-1].split(".")[0], "words": words, "has_image": "image" in rel or "<p:pic>" in xml,
                        "has_bg": "<p:bg>" in xml or "image" in rel, "has_notes": "notesSlide" in rel})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx"); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    pptx = Path(a.pptx).expanduser().resolve()
    outdir = Path(a.out).expanduser() if a.out else Path(tempfile.mkdtemp(prefix="deckcheck-"))
    outdir.mkdir(parents=True, exist_ok=True)
    stats = slide_stats(pptx)
    result = {"pptx": str(pptx), "slides": len(stats), "stats": stats, "warnings": []}
    for s in stats:
        if s["words"] > 80:
            result["warnings"].append(f"slide {s['slide']}: {s['words']} words, too dense")
        if not s["has_bg"]:
            result["warnings"].append(f"slide {s['slide']}: plain white background")
        if not s["has_notes"]:
            result["warnings"].append(f"slide {s['slide']}: no speaker notes")
    if len(stats) < 8:
        result["warnings"].append(f"only {len(stats)} slides; Ben expects a full deck (10+)")
    if not SOFFICE:
        result["warnings"].append("LibreOffice not installed: no visual render (brew install --cask libreoffice)")
    else:
        env = {"HOME": str(outdir), "PATH": "/usr/bin:/bin"}
        r = subprocess.run([SOFFICE[0], "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(pptx)], capture_output=True, text=True, timeout=180, env=env)
        pdf = outdir / (pptx.stem + ".pdf")
        if not pdf.exists():
            result["warnings"].append("PDF render failed: " + (r.stderr or r.stdout)[-200:])
        else:
            result["pdf"] = str(pdf)
            try:
                import pypdfium2 as pdfium
                from PIL import Image
                doc = pdfium.PdfDocument(str(pdf))
                pages = []
                for i in range(len(doc)):
                    img = doc[i].render(scale=1.0).to_pil()
                    p = outdir / f"slide-{i + 1:02d}.png"; img.save(p); pages.append(img)
                result["slides_png"] = [str(outdir / f"slide-{i + 1:02d}.png") for i in range(len(pages))]
                if pages:
                    cols = 3; tw, th = 480, int(480 * pages[0].height / pages[0].width); rows = (len(pages) + cols - 1) // cols
                    sheet = Image.new("RGB", (cols * (tw + 12) + 12, rows * (th + 12) + 12), "white")
                    for i, img in enumerate(pages):
                        sheet.paste(img.resize((tw, th)), (12 + (i % cols) * (tw + 12), 12 + (i // cols) * (th + 12)))
                    cp = outdir / "contact.png"; sheet.save(cp); result["contact_sheet"] = str(cp)
            except Exception as e:
                result["warnings"].append(f"PNG render failed: {e}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
