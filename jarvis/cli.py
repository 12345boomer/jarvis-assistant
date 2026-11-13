"""`jarvis` command: inspect the audit log and spend meter from the terminal."""

from __future__ import annotations

import argparse
import json
import sys

from .audit import AuditLog
from .config import cached_config, config_path, audit_db_path
from .quiet_hours import is_quiet
from .spend import SpendMeter
from .tiers import classify


def _cmd_audit(a: argparse.Namespace) -> None:
    rows = AuditLog().recent(a.limit)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    for r in reversed(rows):
        who = f" {r['approval']}/{r['approver']}" if r.get("approval") else ""
        print(f"{r['ts']}  T{r['tier']} {r['decision']:5}  {r['tool']:<28} {r['rule']:<24}{who}  {r['args_summary'][:60]}")


def _cmd_spend(a: argparse.Namespace) -> None:
    print(json.dumps(SpendMeter(cached_config()).status(), indent=2))


def _cmd_classify(a: argparse.Namespace) -> None:
    args = json.loads(a.args) if a.args else {}
    d = classify(a.tool, args, cached_config().get("permissions"))
    print(f"tier {d.tier}  rule={d.rule}  {d.reason}")


def _cmd_status(a: argparse.Namespace) -> None:
    cfg = cached_config()
    print(f"config:  {config_path()}")
    print(f"audit:   {audit_db_path()}")
    print(f"quiet:   {'yes' if is_quiet(None, cfg) else 'no'}")
    print(f"spend:   {SpendMeter(cfg).status()}")


def _cmd_call(a: argparse.Namespace) -> None:
    from .calls import CallRequest, load_env, place_call
    env = load_env()
    to = a.to or env.get("BEN_PHONE_NUMBER", "")
    if not to:
        print(json.dumps({"status": "error", "why": "no number: set BEN_PHONE_NUMBER in .env or pass --to"})); return
    print(json.dumps(place_call(CallRequest(to_number=to, reason=a.reason, urgent=a.urgent), env=env)))


def _cmd_tasks(a: argparse.Namespace) -> None:
    from . import tasks
    if a.action == "list":
        print(json.dumps(tasks.open_tasks() if not a.all else tasks.load(), indent=2))
    elif a.action == "add":
        print(json.dumps(tasks.add(a.text or "", due=a.due, source="jarvis")))
    elif a.action == "done":
        print(json.dumps(tasks.complete(a.text or "") or {"error": "no such task"}))
    elif a.action == "remove":
        print(json.dumps({"removed": tasks.remove(a.text or "")}))


def _cmd_mac(a: argparse.Namespace) -> None:
    from . import mac
    if a.action == "wake-screen":
        print(json.dumps(mac.run(mac.wake_screen_cmds(open_hud=not a.no_hud), event="mac_wake", detail="wake screen")))
    elif a.action == "open":
        try:
            cmds = mac.open_cmds(a.target or "")
        except ValueError as e:
            print(json.dumps({"status": "error", "why": str(e)})); return
        print(json.dumps(mac.run(cmds, event="mac_open", detail=a.target)))
    elif a.action == "chrome":
        sub_action, arg = (a.target or "tabs"), (a.message or "")
        pid = mac.main_chrome_pid()
        if not pid:
            print(json.dumps({"status": "error", "error": "Ben's Chrome isn't running (the HUD and the hidden Jarvis Browser don't count)"})); return
        try:
            if sub_action == "tabs":
                cmd, ev = mac.chrome_tabs_cmd(pid), "mac_chrome_tabs"
            elif sub_action == "open":
                cmd, ev = mac.chrome_open_cmd(pid, arg), "mac_open"
            elif sub_action == "focus":
                cmd, ev = mac.chrome_focus_cmd(pid, arg), "mac_chrome_focus"
            else:
                raise ValueError("chrome action must be tabs | open -m <url> | focus -m <text>")
        except ValueError as e:
            print(json.dumps({"status": "error", "error": str(e)})); return
        r = mac.run([cmd], event=ev, detail=f"{sub_action} {arg[:80]}".strip(), max_out=60000)   # a tab list is long
        try:
            r["result"] = json.loads(r["steps"][-1].get("out", "null"))
        except (ValueError, IndexError):
            pass
        print(json.dumps(r))
    elif a.action == "volume":
        print(json.dumps(mac.run([mac.volume_cmd(int(a.target or 50))], event="mac_volume", detail=str(a.target))))
    elif a.action == "notify":
        print(json.dumps(mac.run([mac.say_cmd(a.target or "")], event="mac_notify", detail=(a.target or "")[:80])))
    elif a.action == "lock":
        print(json.dumps(mac.run([mac.lock_cmd()], event="mac_lock", detail="display sleep")))
    elif a.action == "brief":
        print(json.dumps(mac.run([mac.brief_cmd()], event="mac_brief", detail="brief on screen")))
    elif a.action == "text":
        try:
            cmd = mac.text_cmd(a.target or "", a.message or "", sms=a.sms)
        except ValueError as e:
            print(json.dumps({"status": "error", "why": str(e)})); return
        print(json.dumps(mac.run([cmd], event="mac_text", detail=f"to {a.target}: {(a.message or '')[:80]}")))
    elif a.action == "contact":
        try:
            cmd = mac.contact_cmd(a.target or "")
        except ValueError as e:
            print(json.dumps({"status": "error", "why": str(e)})); return
        print(json.dumps(mac.run([cmd], event="mac_contact", detail=a.target or "")))


def _cmd_school(a: argparse.Namespace) -> None:
    from . import school
    if a.action == "due":
        print(json.dumps(school.due(days=a.days, limit=a.limit), indent=2))
    elif a.action == "status":
        print(json.dumps(school.whoami()))
    elif a.action == "setup":
        print(json.dumps(school.setup()))
    elif a.action == "api":
        try:
            print(json.dumps(school.api_get(a.target or ""), indent=2)[:20000])
        except ValueError as e:
            print(json.dumps({"status": "error", "why": str(e)}))
    elif a.action == "open":
        # For Ben's eyes: a new tab in his main Chrome (never a new window).
        from . import mac
        url = a.target if (a.target or "").startswith("http") else f"{school.BASE}/ultra/{(a.target or '').lstrip('/')}"
        print(json.dumps(mac.run(mac.open_cmds(url), event="mac_open", detail=url)))
    elif a.action == "login":
        # One-time sign-in: show the Jarvis Browser window on the HuskyCT login page; `setup` hides it again.
        from . import cdp
        print(json.dumps({"visible": cdp.show(f"{school.BASE}/"), "next": "sign in (NetID + Duo), then: jarvis school setup"}))


def _cmd_browser(a: argparse.Namespace) -> None:
    from . import cdp
    if a.action == "start":
        print(json.dumps({"running": cdp.ensure_browser(a.target or "about:blank"), "cdp": cdp.CDP_URL}))
    elif a.action == "status":
        v = cdp.version()
        print(json.dumps({"running": bool(v), "browser": (v or {}).get("Browser", ""), "cdp": cdp.CDP_URL}))
    elif a.action == "open":
        cdp.ensure_browser(a.target or "about:blank")
        cdp.open_url(a.target or "about:blank")
        print(json.dumps({"status": "ok", "note": "hidden tab for Jarvis's own work; use `jarvis mac open <url>` for Ben"}))
    elif a.action == "show":
        print(json.dumps({"visible": cdp.show(a.target or "about:blank")}))
    elif a.action == "hide":
        print(json.dumps({"hidden": cdp.hide()}))


def _cmd_brief_call(a: argparse.Namespace) -> None:
    from . import brief_call
    mode = "dry-run" if a.dry_run else "now" if a.now else "scheduled"
    r = brief_call.run(mode)
    if mode == "dry-run" and not a.json:
        print(r["text"])
        print(f"\n[{r['words']} words · accounts: {', '.join(r['accounts']) or 'none'} · {r['email_candidates']} email candidates"
              + (f" · trimmed {r['trimmed']}" if r.get("trimmed") else "") + (f" · notes: {'; '.join(r['notes'])}" if r.get("notes") else "") + "]")
        return
    if a.cron:
        # No-agent cron job: stdout is delivered to Telegram, so print only when our own Telegram send failed.
        if r.get("status") == "telegram" and not r.get("telegram_sent"):
            print(r.get("text", ""))
        return
    print(json.dumps(r, indent=2, default=str))


def _cmd_cc_backup(a: argparse.Namespace) -> None:
    from . import cc_backup
    if a.action == "status":
        print(json.dumps(cc_backup.status(), indent=2)); return
    r = cc_backup.run()
    if a.cron:
        if r.get("status") == "telegram" and not r.get("sent"):
            print(r.get("reason", ""))
        return
    print(json.dumps(r, indent=2, default=str))


def _cmd_hq(a: argparse.Namespace) -> None:
    from . import hq
    r = hq.ask(a.question)
    print(json.dumps(r) if a.json else r["answer"])


def _cmd_routine(a: argparse.Namespace) -> None:
    from .routines import run
    raise SystemExit(run(a.kind))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("audit", help="recent audit log rows"); s.add_argument("-n", "--limit", type=int, default=50); s.add_argument("--json", action="store_true"); s.set_defaults(fn=_cmd_audit)
    s = sub.add_parser("spend", help="today's model spend vs cap"); s.set_defaults(fn=_cmd_spend)
    s = sub.add_parser("classify", help="show which tier a tool call gets"); s.add_argument("tool"); s.add_argument("args", nargs="?"); s.set_defaults(fn=_cmd_classify)
    s = sub.add_parser("status", help="paths, quiet hours, spend"); s.set_defaults(fn=_cmd_status)
    s = sub.add_parser("tasks", help="Ben's task list: list | add <text> | done <id|text> | remove <id>"); s.add_argument("action", choices=["list", "add", "done", "remove"]); s.add_argument("text", nargs="?"); s.add_argument("--due"); s.add_argument("--all", action="store_true"); s.set_defaults(fn=_cmd_tasks)
    s = sub.add_parser("mac", help="Mac control: wake-screen | open <app|url> | volume <0-100> | notify <text> | lock | brief | text <to> -m <msg> | contact <name> | chrome tabs|open -m <url>|focus -m <text> (Ben's main Chrome)"); s.add_argument("action", choices=["wake-screen", "open", "volume", "notify", "lock", "brief", "text", "contact", "chrome"]); s.add_argument("target", nargs="?"); s.add_argument("--no-hud", action="store_true"); s.add_argument("--message", "-m", default=""); s.add_argument("--sms", action="store_true", help="send as SMS instead of iMessage"); s.set_defaults(fn=_cmd_mac)
    s = sub.add_parser("school", help="HuskyCT: due [--days N] | status | login | setup | api /learn/api/v1/... | open [path|url] (tab in Ben's Chrome)"); s.add_argument("action", choices=["due", "status", "setup", "api", "open", "login"]); s.add_argument("target", nargs="?"); s.add_argument("--days", type=int, default=14); s.add_argument("--limit", type=int, default=12); s.set_defaults(fn=_cmd_school)
    s = sub.add_parser("browser", help="Jarvis Browser (hidden Chrome, CDP on 9333): start | status | open <url> | show <url> (sign-in only) | hide"); s.add_argument("action", choices=["start", "status", "open", "show", "hide"]); s.add_argument("target", nargs="?"); s.set_defaults(fn=_cmd_browser)
    s = sub.add_parser("routine", help="cron pre-run gate: inbox | meetings (prints wakeAgent JSON)"); s.add_argument("kind", choices=["inbox", "meetings"]); s.set_defaults(fn=_cmd_routine)
    s = sub.add_parser("brief-call", help="weekday brief call: --dry-run prints today's brief, --now calls now (test); no flag = the scheduled run"); g = s.add_mutually_exclusive_group(); g.add_argument("--dry-run", action="store_true"); g.add_argument("--now", action="store_true"); s.add_argument("--json", action="store_true"); s.add_argument("--cron", action="store_true", help=argparse.SUPPRESS); s.set_defaults(fn=_cmd_brief_call)
    s = sub.add_parser("cc-backup", help="Claude Code backup call: check (what the 5-minute cron runs) | status (what's pending)"); s.add_argument("action", choices=["check", "status"]); s.add_argument("--cron", action="store_true", help=argparse.SUPPRESS); s.set_defaults(fn=_cmd_cc_backup)
    s = sub.add_parser("hq", help='ask Jessica (HQ agent team) a read-only question: hq ask "how are sales going?"'); s.add_argument("action", choices=["ask"]); s.add_argument("question"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=_cmd_hq)
    s = sub.add_parser("call", help="phone Ben via the voice agent (quiet hours enforced)"); s.add_argument("--reason", required=True); s.add_argument("--urgent", action="store_true"); s.add_argument("--to", default=""); s.set_defaults(fn=_cmd_call)
    a = p.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
