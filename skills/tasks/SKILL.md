---
name: jarvis-tasks
description: "Ben's task list (shown on the HUD). Add, complete, list tasks by voice or chat."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [tasks, todo, hud, jarvis]
---

# Jarvis: tasks

```bash
JARVIS="$HOME/Documents/JARVIS/.venv/bin/jarvis"
$JARVIS tasks list                              # open tasks (JSON)
$JARVIS tasks add "Email Chris the client deck" --due 2026-10-10
$JARVIS tasks done "Email Chris the client deck"  # by id or exact text
$JARVIS tasks remove <id>
```

- "Add X to my list / remind me to X / I need to X" → `tasks add`. Confirm in five words or fewer ("Added. Three open.").
- "What's on my list?" → read the open ones, shortest first, no ids.
- "Done with X / cross off X" → `tasks done`. If no exact match, list the closest two and ask.
- Tasks are Tier 1 (nothing leaves the Mac). The HUD updates within a few seconds.
