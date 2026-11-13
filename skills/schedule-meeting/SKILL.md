---
name: jarvis-schedule-meeting
description: "Find a time and book a Google Calendar event for Ben (after approval), including conflict checks and invite emails."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [calendar, scheduling, jarvis]
    related_skills: [jarvis-google]
---

# Jarvis: schedule a meeting

```bash
GAPI="${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-google/scripts/gapi"
```

Ben is in **America/New_York**. Always use ISO 8601 with the ET offset (`-04:00` in summer, `-05:00` in winter). Ben's day: up at 8 or 9, classes during the day, gym daily; prefers not before 9am.

## Procedure
1. **Check the calendar for the window** before proposing anything:
   ```bash
   $GAPI calendar list --start 2026-10-10T00:00:00-04:00 --end 2026-10-11T00:00:00-04:00
   ```
2. **Propose up to 3 slots** that don't overlap anything, 30 minutes default (or what Ben said). Show them in ET, e.g. "Fri 2:00–2:30pm".
3. **Ask Ben** which slot, and whether to invite anyone (attendee emails). If the other party's email is in the thread, offer it.
4. **Create it.** If Ben gave the time and people, create the event now; if he asked for options, offer slots and create the one he picks.
   ```bash
   $GAPI calendar create --summary "Coffee with Alana" --start 2026-10-10T14:00:00-04:00 --end 2026-10-10T14:30:00-04:00 --attendees "alana@example.com" --location "Zoom"
   ```
   Google emails invites to attendees automatically.
5. **Confirm** with the event id and time. If Ben wants to move or cancel it later: cancel = `$GAPI calendar delete EVENT_ID`.

## Conflicts
If Ben asks for a slot that overlaps an existing event, say what it conflicts with and propose the nearest free alternative. Don't double-book without Ben saying so explicitly.
