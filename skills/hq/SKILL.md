---
name: jarvis-hq
description: "Ask Jessica, Ben's agent team in ~/Documents/HQ, a read-only question: sales, Harvey, leads, prospects, drafts waiting, or anything about an agent (Harvey, Mike, Donna, Louis, Rachel, Katrina, Robert, Alex, Samantha, Faye, Benjamin). Answers only; actions stay in Ben's Jessica chat."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [hq, jessica, harvey, sales, leads, prospects, drafts, agents, team, jarvis]
    related_skills: [jarvis-google]
---

# Jarvis: ask Jessica (HQ)

Jessica is Ben's chief of staff agent, running a team in `~/Documents/HQ` (Harvey = sales, Mike = research, Donna = ops,
Louis = finance, Rachel = contracts, Katrina = client success, Robert = partnerships, Alex = proposals,
Samantha = marketing, Faye = auditor, Benjamin = IT). Use this skill whenever Ben mentions Jessica, "the team", Harvey,
sales, leads, prospects, outreach, drafts, or any of those agents by name: "ask Jessica how sales are going",
"what drafts are waiting", "what's Harvey up to", "has anyone replied".

```bash
JARVIS="$HOME/Documents/JARVIS/.venv/bin/jarvis"
$JARVIS hq ask "how are sales going?"
```

- Pass Ben's question through in his words, in quotes. One call per question; don't ask Jessica follow-ups he didn't ask.
- It takes 30 seconds to 3 minutes (Jessica may delegate to an agent). Run it with the terminal tool's timeout set to
  200 seconds. Tell Ben "Asking Jessica" first if you can.
- The output is her answer as plain text. Relay it as is, briefly; don't add numbers she didn't give. If it starts with
  "Jessica couldn't answer", tell Ben that line.
- Read-only by design: Jessica can't approve, reject, send, pause or change anything from here. If Ben asks for an
  action ("approve the Harbor Dental draft", "pause Harvey"), don't try: tell him to do it in his Jessica chat.
- On a phone call this runs through `ask_jarvis`; if it outlasts the call's limit, the bridge moves it to the background
  and the answer lands on Telegram, like other long actions. Don't use start_task for a simple question.
- It runs on Ben's Claude subscription (Claude Code login), not Jarvis's API credits.
