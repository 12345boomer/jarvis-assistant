"""Permission tiers: classify a tool call as 1 (do), 2 (ask), or 3 (never).

Rules come from config/jarvis.yaml -> permissions. Matching order, first hit wins:
  1. exact-name overrides
  2. any argument key in tier3_arg_keys            -> tier 3
  3. tool name matches a tier3_never glob           -> tier 3
  4. terminal command matches tier2_terminal regex  -> tier 2
  5. tool name matches a tier2_ask glob             -> tier 2
  6. tool name matches a tier1_do glob              -> tier 1
  7. default_tier
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

DO, ASK, NEVER = 1, 2, 3

_DEFAULTS: dict[str, Any] = {
    "default_tier": ASK,
    "overrides": {},
    "tier3_arg_keys": [],
    "tier3_never": [],
    "tier3_terminal_patterns": [],
    "tier2_terminal_patterns": [],
    "tier1_terminal_patterns": [],   # explicit allowlist checked before tier2 terminal patterns
    "arg_rules": [],                 # [{tools: [glob...], tier: 2|3, pattern: regex over JSON(args), reason}]
    "tier2_ask": [],
    "tier1_do": [],
}


@dataclass(frozen=True)
class Decision:
    tier: int
    rule: str  # which rule matched, for the audit log
    reason: str


def _walk_keys(obj: Any) -> Iterable[str]:
    """Yield every dict key in a nested structure, lowercased."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k).lower()
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_keys(v)


def _match_any(name: str, patterns: Iterable[str]) -> str | None:
    for pat in patterns:
        if fnmatch.fnmatchcase(name, pat.lower()):
            return pat
    return None


def classify(tool_name: str, args: dict[str, Any] | None, permissions: dict[str, Any] | None) -> Decision:
    p = {**_DEFAULTS, **(permissions or {})}
    name = (tool_name or "").lower()
    args = args or {}

    if name in {k.lower(): v for k, v in (p["overrides"] or {}).items()}:
        tier = int({k.lower(): v for k, v in p["overrides"].items()}[name])
        return Decision(tier, f"override:{name}", "explicit override in jarvis.yaml")

    bad_keys = {k.lower() for k in p["tier3_arg_keys"]}
    for key in _walk_keys(args):
        if key in bad_keys:
            return Decision(NEVER, f"arg:{key}", f"argument '{key}' looks like a credential or financial detail")

    if pat := _match_any(name, p["tier3_never"]):
        return Decision(NEVER, f"tier3:{pat}", "money, deletion, or credentials are never automated")

    # Per-tool argument rules (e.g. computer_use typing a card number -> never; typing "place order" -> ask).
    try:
        blob = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        blob = str(args)
    for rule in p["arg_rules"] or []:
        tools = rule.get("tools") or ["*"]
        if any(fnmatch.fnmatchcase(name, t.lower()) for t in tools) and re.search(rule["pattern"], blob, flags=re.IGNORECASE):
            tier = int(rule.get("tier", ASK))
            return Decision(tier, f"arg_rule:{rule.get('name', rule['pattern'][:30])}", rule.get("reason", "argument rule"))

    if name in ("terminal", "execute_code", "process_manage"):
        cmd = str(args.get("command") or args.get("code") or "")
        for rx in p["tier3_terminal_patterns"]:
            if re.search(rx, cmd, flags=re.IGNORECASE):
                return Decision(NEVER, f"terminal3:{rx}", "shell command would permanently delete or move money")
        for rx in p["tier1_terminal_patterns"]:
            if re.search(rx, cmd, flags=re.IGNORECASE):
                return Decision(DO, f"terminal1:{rx}", "explicitly allowed shell command")
        for rx in p["tier2_terminal_patterns"]:
            if re.search(rx, cmd, flags=re.IGNORECASE):
                return Decision(ASK, f"terminal:{rx}", "shell command can send, change, or publish something")

    if pat := _match_any(name, p["tier2_ask"]):
        return Decision(ASK, f"tier2:{pat}", "this action is visible to other people or changes a schedule")

    if pat := _match_any(name, p["tier1_do"]):
        return Decision(DO, f"tier1:{pat}", "read-only or draft work")

    tier = int(p["default_tier"])
    return Decision(tier, "default", "no rule matched; using default_tier")
