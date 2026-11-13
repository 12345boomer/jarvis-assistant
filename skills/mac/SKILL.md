---
name: jarvis-mac
description: "Control Ben's Mac: wake the screen, open apps and websites, adjust volume, and operate apps with computer_use. Includes the 'turn on / unlock my laptop' behavior."
version: 0.1.0
author: Ben Eydlin
license: MIT
metadata:
  hermes:
    tags: [mac, computer-use, apps, jarvis]
---

# Jarvis: the Mac

```bash
JARVIS="$HOME/Documents/JARVIS/.venv/bin/jarvis"
```

## Quick actions (Tier 1, just do them)
| Ben says | Run |
|---|---|
| "turn on / wake up / unlock my laptop", "open the HUD" | `$JARVIS mac wake-screen` → screen comes on and the HUD opens. Then say: "Screen's on. If it's locked, that one's yours." Never try to type his password; the lock screen is his. |
| "open Spotify" / "open Notes" | `$JARVIS mac open "Spotify"` (`open -a` under the hood) |
| "open youtube.com", "go to x.com" | `$JARVIS mac open "https://youtube.com"` → new tab in **Ben's main Chrome window** (never a new window) |
| "volume 30" / "mute" | `$JARVIS mac volume 30` / `$JARVIS mac volume 0` |
| "turn the screen off" | `$JARVIS mac lock` (display sleep; macOS locks per Ben's setting) |
| desktop toast | `$JARVIS mac notify "text"` |
| "text Chris that I'm running late" | 1) `$JARVIS mac contact "Chris"` → phones/emails (Tier 1). 2) Draft the text in Ben's voice (short, casual; one version is fine for a text unless he asks for options), say who and what, and send: 3) `$JARVIS mac text "+12035551234" -m "Running 10 late, sorry"`. Add `--sms` only if the person isn't on iMessage. Never guess a number; if Contacts has several, ask which. |

If the Mac is asleep, none of this runs (Jarvis lives on it). Tell Ben plainly if a command returns an error.

## Ben's Chrome tabs (the only way to look at or use his browser)
Three Chrome processes run on this Mac: Ben's own, the HUD (`jarvis-hud-chrome`) and Jarvis's hidden browser. AppleScript's
`tell application "Google Chrome"` and a bare `open <url>` hit one of them at random (that is how "check my TradingView tab"
once saw only the HUD and reported "only one tab open"). Always go through these, which target Ben's Chrome by process id:

| Need | Run |
|---|---|
| what tabs does Ben have open? | `$JARVIS mac chrome tabs` → JSON `[{window, index, title, url, active}]` |
| bring his TradingView / Gmail / HuskyCT tab to the front | `$JARVIS mac chrome focus -m tradingview` (part of the URL or title; prints the tab or `not found`) |
| open a page for him | `$JARVIS mac chrome open -m "https://..."` (new tab in his front window) or `$JARVIS mac open <url>` (same thing) |

Then operate the page with `computer_use` (`capture` first). If `focus` says `not found`, say so and open the page with
`chrome open` when that is what Ben wants; never conclude "Ben has only one tab" from AppleScript or from the HUD.
Jarvis's hidden browser (`jarvis browser ...`) is for Jarvis's own logged-in work (HuskyCT), not for Ben's tabs.

## Operating apps: `computer_use`
Use `computer_use` for anything inside an app or window: `capture` (screenshot with numbered elements), `click element=N`, `type text=...`, `key keys=...`, `scroll`. It works in the background; Ben's cursor doesn't move. For web pages prefer the `browser` toolset.

Rules of engagement:
- Read before you click: `capture` first, act on what you actually see, `capture` again after anything that changes the screen.
- **Irreversible clicks:** Send, Submit, Post, Publish are fine when they're the thing Ben asked for. Buy, Pay, Place order, Checkout, Delete account, Sign out: never (Tier 3), prepare the page and tell Ben it's ready for his click. Reading, navigating, searching, filling text, and opening things need no confirmation.
- Never type a password, card number, CVV, SSN, or 2FA code into anything. If a login or payment form appears, tell Ben it's his turn and wait.
- Don't install software, change system settings, or touch Keychain/System Settings/Terminal via computer_use. Use `jarvis mac` or ask.
- If the screen shows something unexpected (dialog, error, different app), say so instead of guessing.
- One task at a time; report what you did in one or two lines.

## "Do some work for me" pattern
1. Restate the goal in one line. 2. `capture` the relevant app. 3. Do the steps, confirming only at irreversible points. 4. `capture` to verify. 5. Report.
