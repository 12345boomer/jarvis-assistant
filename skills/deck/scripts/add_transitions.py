#!/usr/bin/env python3
"""add_transitions.py DECK.pptx [fade|push|wipe|morph] [duration_ms]
Adds the same slide transition to every slide (pptxgenjs can't). Rewrites the file in place."""
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

KINDS = {"fade": '<p:fade/>', "push": '<p:push dir="l"/>', "wipe": '<p:wipe dir="l"/>', "morph": None}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 2
    path = Path(sys.argv[1]); kind = sys.argv[2] if len(sys.argv) > 2 else "fade"; dur = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    if kind not in KINDS:
        print(f"unknown transition {kind}"); return 2
    if kind == "morph":
        trans = f'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:Choice xmlns:p159="http://schemas.microsoft.com/office/powerpoint/2015/09/main" Requires="p159"><p:transition spd="slow" p14:dur="{dur}" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"><p159:morph option="byObject"/></p:transition></mc:Choice><mc:Fallback><p:transition spd="slow"><p:fade/></p:transition></mc:Fallback></mc:AlternateContent>'
    else:
        trans = f'<p:transition spd="slow" p14:dur="{dur}" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main">{KINDS[kind]}</p:transition>'
    tmp = Path(tempfile.mkdtemp()) / path.name
    n = 0
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", item.filename):
                xml = data.decode("utf-8")
                xml = re.sub(r"<p:transition.*?</p:transition>|<p:transition[^>]*/>", "", xml, flags=re.S)
                xml = re.sub(r"<mc:AlternateContent>.*?</mc:AlternateContent>", "", xml, flags=re.S) if "p159:morph" in xml else xml
                # transition goes after </p:clrMapOvr> (or after </p:cSld> if there is none)
                if "</p:clrMapOvr>" in xml:
                    xml = xml.replace("</p:clrMapOvr>", "</p:clrMapOvr>" + trans, 1)
                else:
                    xml = xml.replace("</p:cSld>", "</p:cSld>" + trans, 1)
                data = xml.encode("utf-8"); n += 1
            zout.writestr(item, data)
    shutil.move(str(tmp), str(path))
    print(f"{kind} transition on {n} slides -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
