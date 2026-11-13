"""Hook callbacks for the jarvis-guard Hermes plugin.

Wired by plugins/jarvis_guard/__init__.py -> register(ctx). Everything here must
never raise: a crashing pre_tool_call hook blocks the tool (Hermes fails closed),
so each callback catches its own errors and logs them.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from . import fence
from .audit import AuditLog
from .config import cached_config
from .spend import SpendMeter
from .tiers import ASK, DO, NEVER, classify

logger = logging.getLogger("jarvis.guard")

_audit: AuditLog | None = None
_spend: SpendMeter | None = None
_warned_level: str = "ok"  # so the spend warning is injected once per level change


def _get_audit() -> AuditLog:
    global _audit
    if _audit is None:
        _audit = AuditLog()
    return _audit


def _get_spend() -> SpendMeter:
    global _spend
    if _spend is None:
        _spend = SpendMeter(cached_config())
    return _spend


# ---- pre_tool_call: tiering ------------------------------------------------

def _tier2_do(cfg: dict[str, Any]) -> bool:
    return str((cfg.get("permissions") or {}).get("tier2_mode", "ask")).lower() == "do"


def _unattended(session_id: str, task_id: str) -> bool:
    """Hermes cron runs: session ids look like cron_<job>_<stamp>, task ids like cron:<job>:<run>. Nobody is in the
    loop there, so Tier 2 keeps its approval prompt (which Hermes's cron_mode: deny turns into a refusal)."""
    s, t = (session_id or ""), (task_id or "")
    return s.startswith("cron_") or t.startswith("cron:") or s.startswith("cron:")


# ---- the hidden Jarvis Browser is for signed-in sites only ------------------
# Ben, 2026-11-06: "things try to open in that hidden browser and then I can't see it". Hermes's browser_* tools drive
# the headless Jarvis Browser, which Ben never sees and which holds none of his tabs. So they may only touch the sites
# Ben signed in to there (permissions.hidden_browser_hosts); any other page is blocked with directions to the right
# tool. The rule lived only in skill text before, and the model sometimes ignored it.

# Any scheme-ful or scheme-relative URL in the arguments. Hosts come from urllib's parser (userinfo and port stripped),
# so "https://lms.uconn.edu:x@evil.com" resolves to evil.com, not uconn.
_URL = re.compile(r"(?:\b[a-z][a-z0-9+.-]*:)?//[^\s\"'\\)<>]+", re.I)
_LOCAL_SCHEME = re.compile(r"\b(?:file|javascript|vbscript|filesystem|blob):|\bdata:[^\s,'\"]*,", re.I)   # data needs its comma: not `{data: 1}`
DEFAULT_HIDDEN_HOSTS = ["uconn.edu"]

HIDDEN_BROWSER_BLOCK = (
    "Blocked: the browser tool drives Jarvis's hidden browser. Ben can't see it, and none of his tabs are in it. "
    "To show Ben a page, run `$HOME/Documents/JARVIS/.venv/bin/jarvis mac open <url>` (new tab in his own Chrome). "
    "To see his open tabs: `jarvis mac chrome tabs`. To read a page yourself: web_extract. "
    "The hidden browser is only for sites Ben signed in to there ({hosts}).")
HIDDEN_BROWSER_NOTE = ("[Jarvis guard: this came from Jarvis's hidden browser. Ben can't see it, and it is not his Chrome "
                       "or his tabs. For his tabs: `jarvis mac chrome tabs`; to show him a page: `jarvis mac open <url>`.]")


_URL_KEYS = ("url", "href", "target", "page", "site", "link")


def _url_key_host(v: str) -> str | None:
    """A url-ish argument is checked structurally: only http(s) with a host passes to the allowlist check; any other
    scheme (file:, javascript:, data:, ...) or an unparsable value is reported as a violation."""
    if not v or v in ("about:blank",):
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*:|^//", v, re.I):
        v = "https://" + v                                   # bare domain: "youtube.com"
    try:
        u = urlsplit("https:" + v if v.startswith("//") else v)
        host = u.hostname
    except ValueError:
        return "?unparsable"
    if u.scheme.lower() not in ("http", "https"):
        return f"?{u.scheme.lower()}-scheme"
    return (host or "?unparsable").rstrip(".").lower()


def _hosts_in(args: Any) -> set[str]:
    """Every web host a browser tool's arguments point at: url-ish keys parsed structurally, plus any URL inside
    free text or code (after the WHATWG clean-up: tab/CR/LF removed, backslash read as slash)."""
    found: set[str] = set()
    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k).lower())
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif isinstance(v, str):
            clean = re.sub(r"[\t\r\n]", "", v) if key in _URL_KEYS else re.sub(r"/{2,}", "//", v.replace("\\", "/"))
            if key in _URL_KEYS:
                h = _url_key_host(clean.strip())
                if h:
                    found.add(h)
                return
            for u in _URL.findall(clean):
                try:
                    host = urlsplit(u if not u.startswith("//") else "https:" + u).hostname
                except ValueError:
                    host = None
                found.add((host or "?unparsable").rstrip(".").lower())
            if _LOCAL_SCHEME.search(clean):
                found.add("?local-scheme")
    walk(args or {})
    return found


def hidden_browser_violation(tool_name: str, args: Any, allowed: list[str] | None = None) -> str | None:
    """The first host a browser_* call would take the hidden browser to that isn't a signed-in site, else None."""
    if not (tool_name or "").lower().startswith("browser"):
        return None
    allowed = [h.lower().strip(".") for h in (allowed if allowed is not None else DEFAULT_HIDDEN_HOSTS) if h and h.strip(".")]
    for host in sorted(_hosts_in(args)):
        if host in ("127.0.0.1", "localhost"):
            continue
        if not any(host == a or host.endswith("." + a) for a in allowed):
            return host
    return None


def pre_tool_call(tool_name: str = "", args: dict[str, Any] | None = None, task_id: str = "",
                  session_id: str = "", tool_call_id: str = "", platform: str = "", **_: Any):
    try:
        cfg = cached_config()
        d = classify(tool_name, args, cfg.get("permissions"))
        over_cap = _get_spend().status()["level"] == "over"
        hosts = (cfg.get("permissions") or {}).get("hidden_browser_hosts") or DEFAULT_HIDDEN_HOSTS
        off_site = hidden_browser_violation(tool_name, args, hosts)

        if off_site:
            decision, directive = "block", {"action": "block", "message": HIDDEN_BROWSER_BLOCK.format(hosts=", ".join(hosts))}
            d = d.__class__(d.tier, "hidden-browser", f"hidden browser asked to open {off_site}")
        elif d.tier == NEVER:
            decision, directive = "block", {
                "action": "block",
                "message": f"Jarvis will not do this itself ({d.reason}). Prepare it and hand it to Ben to do.",
            }
        elif over_cap and tool_name not in ("memory", "clarify"):
            decision, directive = "block", {
                "action": "block",
                "message": "Daily spend cap reached. Jarvis is read-only until midnight ET; tell Ben.",
            }
        elif d.tier == ASK and _tier2_do(cfg) and not _unattended(session_id, task_id):
            # Ben asked for it in a conversation: do it. (Cron/unattended runs keep the prompt, which Hermes denies.)
            decision, directive = "allow", None
            d = d.__class__(d.tier, d.rule + "+tier2-do", d.reason)
        elif d.tier == ASK:
            decision, directive = "ask", {
                "action": "approve",
                "message": f"Needs Ben's OK: {tool_name} — {d.reason}",
                "rule_key": f"jarvis:{d.rule}",
            }
        else:
            decision, directive = "allow", None

        _get_audit().record_call(tool=tool_name, args=args, tier=d.tier, decision=decision, rule=d.rule,
                                 reason=d.reason, session_id=session_id or task_id, task_id=task_id,
                                 tool_call_id=tool_call_id, platform=platform)
        return directive
    except Exception as e:  # never let the guard itself take the agent down
        logger.exception("jarvis-guard pre_tool_call failed: %s", e)
        return None


# ---- post_tool_call: outcome -----------------------------------------------

def post_tool_call(tool_name: str = "", args: dict[str, Any] | None = None, result: str = "", task_id: str = "",
                   duration_ms: int = 0, session_id: str = "", tool_call_id: str = "", **_: Any):
    try:
        ok: bool | None = None
        if isinstance(result, str):
            ok = not result.lstrip().startswith('{"error"')
        _get_audit().finish_call(session_id=session_id or task_id, tool_call_id=tool_call_id, tool=tool_name,
                                 duration_ms=int(duration_ms or 0), ok=ok)
    except Exception as e:
        logger.exception("jarvis-guard post_tool_call failed: %s", e)


# ---- untrusted content fencing ---------------------------------------------

def transform_tool_result(tool_name: str = "", args: dict[str, Any] | None = None, result: str = "",
                          task_id: str = "", **_: Any):
    try:
        out = fence.transform(tool_name, args, result)
        if (tool_name or "").lower().startswith("browser") and isinstance(out if out is not None else result, str):
            out = f"{out if out is not None else result}\n{HIDDEN_BROWSER_NOTE}"
        if out is not None and fence.looks_like_injection(result):
            _get_audit().event("injection_flag", f"{tool_name}: instruction-like phrases in tool output")
        return out
    except Exception as e:
        logger.exception("jarvis-guard transform_tool_result failed: %s", e)
        return None


# ---- approvals: who said yes -----------------------------------------------

def post_approval_response(command: str = "", choice: str = "", session_key: str = "", surface: str = "",
                           decided_by: str = "", **_: Any):
    try:
        approver = "aux_llm" if decided_by == "aux_llm" else f"ben:{surface or 'unknown'}"
        _get_audit().record_approval(session_id=session_key, choice=choice, approver=approver)
    except Exception as e:
        logger.exception("jarvis-guard post_approval_response failed: %s", e)


# ---- spend meter -----------------------------------------------------------

def post_api_request(model: str = "", usage: dict[str, Any] | None = None, **_: Any):
    try:
        if usage:
            _get_spend().record(model, usage)
    except Exception as e:
        logger.exception("jarvis-guard post_api_request failed: %s", e)


def pre_llm_call(**_: Any):
    """Inject a one-line spend warning when crossing 80% / 100% of the daily cap."""
    global _warned_level
    try:
        st = _get_spend().status()
        if st["level"] != _warned_level:
            _warned_level = st["level"]
            if st["level"] == "warn":
                _get_audit().event("spend_alert", f"{st['usd']:.2f} of {st['cap']:.2f}")
                return {"context": f"[Jarvis system] Model spend today is ${st['usd']:.2f} of the ${st['cap']:.2f} daily cap. Mention this to Ben once, briefly."}
            if st["level"] == "over":
                _get_audit().event("spend_cap_hit", f"{st['usd']:.2f} of {st['cap']:.2f}")
                return {"context": "[Jarvis system] Daily spend cap reached. Tools are blocked until midnight ET. Tell Ben and keep replies short."}
    except Exception as e:
        logger.exception("jarvis-guard pre_llm_call failed: %s", e)
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("post_approval_response", post_approval_response)
    ctx.register_hook("post_api_request", post_api_request)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    logger.info("jarvis-guard registered (config=%s)", cached_config().get("timezone"))
