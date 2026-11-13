---
name: jarvis-deck
description: "Build a presentation the way the Claude app would: designed slides (gradient backgrounds, icons, charts, stat cards, case studies, quiz), real facts, speaker notes, transitions, visually checked before delivery. Saves to ~/Downloads/Jarvis. Never a plain white bullet deck."
version: 0.2.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [presentation, pptx, slides, deck, pptxgenjs, jarvis]
    related_skills: [jarvis-google, jarvis-huskyct]
---

# Jarvis: make a deck (house style)

Ben rejected the first deck as "two white slides with three bullets". The bar is the Claude app: every slide designed, real numbers, a story. If it would embarrass him in front of the room, it is not done.

```bash
SK="${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-deck/scripts"
PY="${HERMES_HOME:-$HOME/.hermes}/jarvis-deck/.venv/bin/python"
```

## Procedure (do all of it; don't ask Ben questions first)
1. **Brief → outline.** Decide audience, length and arc yourself: 10 to 16 slides for a 20 to 30 minute talk. Arc: title → why it matters (stat slide) → 2 or 3 sections, each with a concept slide, a real case or chart, → quiz or interactive moment → closing with next step. Audience defaults: OFC = UConn students, concrete, fun, one company story per concept. OceanCrest = buyers, professional, outcomes. Class = academic.
2. **Facts.** Every number and company fact comes from a web search (dates, amounts, what happened). Budget: **at most 6 searches** for a deck; batch several questions into one search, use `web_extract` on one good source rather than many searches. Write sources into the notes. No invented statistics. If unsure, say "about" and note "verify".
3. **Write `deck.js` by copying the template.** `cp "$SK/deck_template.js" /tmp/deck.js`, keep the helpers, replace `THEME` (palette per audience) and `CONTENT` + the slide calls at the bottom. Mix slide types: `titleSlide`, `statSlide` (big numbers), `sectionSlide`, `bulletSlide` (max 5 bullets, under 12 words each, give it an `iconName`), `chartSlide` (native chart; use real series), `caseSlide` (company story + lesson), `quizSlide`, `closingSlide`. Never more than two bullet slides in a row. Every slide gets `notes` (what Ben says, 2 to 4 sentences). Set `DECK_OUT` to `~/Downloads/Jarvis/<slug>.pptx`.
   - Icons: react-icons names (`FaChartLine`, `FaLightbulb`, `MdSchool`, `LuTarget`, ...). Backgrounds are generated gradients (`bg()`), no network needed.
   - pptxgenjs rules: hex colors without `#`; `bullet: true` per item, never a literal "•"; one `new pptxgen()` per file; `isTextBox: true` on text; `rectRadius` only on `roundRect`.
4. **Build:** `DECK_OUT=~/Downloads/Jarvis/<slug>.pptx "$SK/deckjs" /tmp/deck.js` (adds fade transitions automatically).
5. **Look at it before delivering.** `"$PY" "$SK/deck_check.py" ~/Downloads/Jarvis/<slug>.pptx` renders every slide; open `contact.png` with the vision tool and check: nothing overflowing or overlapping, no empty-looking slide, no wall of text, consistent palette. Fix `deck.js` and rebuild until the warnings list is empty and it looks right. This step is not optional.
6. **Deliver:** one short message: title, slide count, the path as `Downloads/Jarvis/<slug>.pptx`, the two or three assumptions you made. No Telegram attachment, no Drive upload unless asked. If he named recipients, email it now (`$GAPI gmail send --attach`); otherwise say "send it to <who>" is how he gets it emailed.
7. **Edits:** "change slide 4" means edit `/tmp/deck.js` (keep it; or re-copy and rebuild), rebuild, re-check.

## Never
- Never deliver under 8 slides, a white-background bullet deck, or a deck you didn't render and look at.
- Never trim the deck to make it "fit"; make the content fit the design instead (split slides).
- Never open PowerPoint on Ben's screen unless he asks ("open it" → `open -a "Microsoft PowerPoint" <path>`).
