---
name: jarvis-huskyct
description: "HuskyCT (UConn's Blackboard Learn): what's due, open courses, read content, build study guides, prepare (and with approval submit) assignments. Uses Ben's logged-in Jarvis Browser; never his password."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [huskyct, blackboard, uconn, school, assignments, study, jarvis]
    related_skills: [jarvis-deck, jarvis-google, jarvis-mac]
---

# Jarvis: HuskyCT

HuskyCT = Blackboard Learn **Ultra** at `https://lms.uconn.edu`. Ben's account: `your-netid@uconn.edu` (NetID SSO + Duo).

```bash
JARVIS="$HOME/Documents/JARVIS/.venv/bin/jarvis"
```

## Three ways in (cheapest first)
1. **Due dates (no browser):** `$JARVIS school due [--days 14]` → JSON items `{course, title, due, due_h, urgency, url}` from Ben's private calendar feed. Tier 1, instant. Use this for "what's due", "any homework this week", study planning.
2. **Blackboard's JSON API through the logged-in browser:** `$JARVIS school api /learn/api/v1/users/me/memberships?expand=course` (courses), then explore per course, e.g. `/learn/api/v1/courses/<courseId>/contents?recursive=true` (content tree), `/learn/api/v1/courses/<courseId>/gradebook/columns` (assignments + weights), `/learn/api/v1/courses/<courseId>/gradebook/grades?userId=me`. GET only; Tier 1. Check login first with `$JARVIS school status`.
3. **The browser itself:** the `browser` tool is attached to the hidden **Jarvis Browser** (signed in to HuskyCT). Navigate to `https://lms.uconn.edu/ultra/courses`, open a course, read pages, download files, fill in assignment text. Nothing appears on Ben's screen; when he should see a page, `$JARVIS school open courses` opens it as a tab in his main Chrome.

## Login (Ben's job, once)
If `school status` says not logged in: run `$JARVIS school login` (shows the Jarvis Browser window on the HuskyCT login page, the only time that window is visible) and tell Ben: "Sign in to HuskyCT in the window that just opened (NetID + Duo); say done and I'll finish." Then `$JARVIS school setup` (stores the feed link and hides the window again). **Never type his password**; clicking the saved account in the picker is fine. Sessions last weeks.

## Windows and tabs (Ben's rule)
Jarvis never opens new browser windows; the HUD is the only window of its own. The Jarvis Browser is hidden; it is for Jarvis's own reading and clicking. Anything Ben should look at opens as a new tab in his main Chrome: `$JARVIS mac open "https://lms.uconn.edu/ultra/courses"` or `$JARVIS school open courses`.

## Jobs Ben asks for
- **"What's due?"** → `school due`, answer soonest-first with hours left; offer to add the top one to tasks.
- **"Make me a study guide for X"** → pull the course content (API or browser): syllabus, slides, readings, assignment prompts; download files to `~/Downloads/Jarvis/<course>/`; write the guide (key concepts, formulas, worked examples, likely exam questions with answers) and save it as a `.docx` or `.pptx` (jarvis-deck) in `~/Downloads/Jarvis/`. Say the path.
- **"Help with / draft assignment X"** → read the prompt and rubric, draft in a file in `~/Downloads/Jarvis/`, tell Ben what you assumed. Drafting is Tier 1.
- **Submitting on HuskyCT** (upload, "Submit", discussion posts): do it when Ben asked for the submission; say what went in. Never start a timed quiz or exam unless he explicitly said to.
- **Grades** → gradebook API per course; give the number and what's still ungraded.

## Rules
- Dates from the feed are authoritative; if the API and feed disagree, say so.
- Course codes: FNCE/ACCT/ECON/... + 4 digits; keep them in answers so Ben knows which class.
- Don't paste huge content trees into chat; summarize and keep files in Downloads/Jarvis.
