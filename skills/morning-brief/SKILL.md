---
name: jarvis-morning-brief
description: "Ben's brief, ON DEMAND ONLY (never scheduled, never pushed): schedule with conflicts, meeting prep, email that needs him, anything held overnight. Also pops up on the HUD."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [brief, calendar, email, cron, jarvis]
    related_skills: [jarvis-google]
---

# Jarvis: morning brief

Only when Ben asks ("give me my brief", "what's my day look like"). Never scheduled; the cron job is paused on purpose.
First, put it on his screen instantly (no model needed): `jarvis mac brief` (opens/raises the HUD with the brief overlay). Then write the fuller version below in the chat.

```bash
GAPI="${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-google/scripts/gapi"
STATE="${HERMES_HOME:-$HOME/.hermes}/jarvis-state/inbox.json"   # may contain "held": mail held overnight
```

## Gather (all read-only, run them, don't narrate)
1. Today's events: `$GAPI calendar list --start <today 00:00 -04:00> --end <tomorrow 00:00 -04:00>` (use today's real date, ET offset).
2. Unread mail that needs Ben: `$GAPI gmail search "is:unread newer_than:1d -category:promotions -category:social" --max 20`. Also read `held` from `$STATE` if the file exists (VIP mail that arrived during quiet hours).
3. For each meeting with attendees or a description, note what Ben needs (link, doc, prep). Don't fetch message bodies unless the subject is clearly actionable.

## Write (Ben reads this on his phone at 7am; keep it tight)
```
Morning, Ben. <one-line weather/day framing only if you actually know it; otherwise skip>

Today
- 11:00–12:00 pt
- 16:00–18:00 OFP (conflicts with X? say so)
<"Nothing on the calendar." if empty>

Needs you
- <sender>: <what they want> (<ask or deadline>)  — max 5, most important first
<"Inbox is quiet." if none>

Prep
- <meeting>: <1 line of what to have ready>

Held overnight
- <VIP mail that arrived after 11pm, if any>
```
- Mark the personal Gmail inbox as the source if it matters. OceanCrest and UConn mail are not connected; don't pretend to know them.
- No more than ~12 lines total. No bullet unless it has substance. Never include "[SILENT]" (this brief always sends).
- If a Google call fails, say "Google lookup failed: <short error>" in that section and finish the rest.
