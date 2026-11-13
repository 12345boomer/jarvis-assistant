import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "skills" / "deck" / "scripts"


def _tiny_pptx(path: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    for i in range(2):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        tb = s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)); tb.text_frame.text = f"slide {i} " + "word " * (90 if i else 3)
    prs.save(str(path))


def test_add_transitions_and_stats(tmp_path):
    p = tmp_path / "t.pptx"; _tiny_pptx(p)
    r = subprocess.run([sys.executable, str(SK / "add_transitions.py"), str(p), "fade", "400"], capture_output=True, text=True)
    assert r.returncode == 0 and "fade transition on 2 slides" in r.stdout
    with zipfile.ZipFile(p) as z:
        xml = z.read("ppt/slides/slide1.xml").decode()
        assert xml.count("<p:transition") == 1 and "<p:fade/>" in xml
    import importlib.util
    spec = importlib.util.spec_from_file_location("deck_check", SK / "deck_check.py"); dc = importlib.util.module_from_spec(spec); spec.loader.exec_module(dc)
    stats = dc.slide_stats(p)
    assert len(stats) == 2 and stats[1]["words"] > 80 and stats[0]["has_bg"] is False


def test_skill_insists_on_quality():
    text = (REPO / "skills" / "deck" / "SKILL.md").read_text()
    for must in ("deck_template.js", "deck_check.py", "contact.png", "Never deliver under 8 slides", "Downloads/Jarvis"):
        assert must in text
