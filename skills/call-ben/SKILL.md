---
name: jarvis-call-ben
description: "Phone Ben through the voice agent for things that can't wait. Quiet hours enforced by the script, not by you."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [phone, voice, urgent, jarvis]
---

# Jarvis: call Ben

Use this only when something needs Ben's decision or attention sooner than his next look at Telegram: a VIP's time-sensitive email, a meeting conflict in the next two hours, or when Ben explicitly asked to be called.

```bash
JARVIS="$HOME/Documents/JARVIS/.venv/bin/jarvis"
$JARVIS call --reason "Chris K needs a yes on the client contract by 3pm; two conflicting slots at 2."
$JARVIS call --reason "..." --urgent        # only if it meets the urgent rules in config/jarvis.yaml
```

Rules:
- `--reason` is the first thing Ben hears. One or two sentences, the decision he needs to make, no preamble.
- Quiet hours (23:00–07:00 ET): the script refuses non-urgent calls and returns `"status": "suppressed"`. Don't retry; send a Telegram message instead and include it in the morning brief.
- Pass `--urgent` only when the item matches the urgent rules (VIP sender + time-sensitive phrase, or meeting within 2h with a conflict). Don't pass it because Ben sounds busy.
- Calling anyone other than Ben (`--to`) requires Ben's approval and a clear reason.
- `"status": "error"` → tell Ben what failed (missing config vs. ElevenLabs/Twilio error); don't loop.
- Every call attempt is logged to the audit DB (`call_placed`, `call_suppressed_quiet_hours`, `call_failed`).
