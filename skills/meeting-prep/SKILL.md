---
name: jarvis-meeting-prep
description: "Send Ben a short prep note ~30 minutes before a meeting: who, what, related email, conflicts. Triggered by the cron gate."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [calendar, prep, cron, jarvis]
    related_skills: [jarvis-google, jarvis-call-ben]
---

# Jarvis: meeting prep

A pre-run gate (`jarvis routine meetings`) found events starting in 20–45 minutes that haven't been prepped. Fields in the script output: `meetings[]` (summary, start, end, location, attendees, description, minutes_until, conflicts_with), `urgent_conflicts[]`, `quiet_hours`.

```bash
GAPI="${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-google/scripts/gapi"
JARVIS="$HOME/Documents/JARVIS/.venv/bin/jarvis"
```

## Procedure (per meeting, keep it under 6 lines)
1. If there are attendees, search recent mail with them: `$GAPI gmail search "from:<email> OR to:<email> newer_than:14d" --max 5`. Pull the gist only.
2. If the description has a link or doc name, mention it; don't open Drive unless a doc is named.
3. **Reply with the Telegram note:**
   ```
   In <N> min: <summary> (<start–end ET>, <location or link>)
   With: <names>
   Context: <1–2 lines from recent email, or "no recent thread">
   Conflicts with: <other event>  <- only if conflicts_with is non-empty
   ```
4. If `urgent_conflicts` is non-empty (two things at once within the next two hours): say which to keep and why, and ask "Move the other?" If `quiet_hours` is false and Ben hasn't answered a previous conflict note, you may call: `$JARVIS call --reason "<A> and <B> overlap at <time>. I'd keep <A>. Move <B>?" --urgent`. One call per conflict, ever.
5. Solo events (gym, "pt", study blocks, classes with no attendees): respond with only `[SILENT]` unless there's a conflict.
