import pytest

from jarvis.tiers import ASK, DO, NEVER, classify


@pytest.mark.parametrize("tool,args,tier", [
    # Tier 1: read / search / draft
    ("gmail_search_messages", {"q": "from:x"}, DO),
    ("calendar_list_events", {}, DO),
    ("read_file", {"path": "/x"}, DO),
    ("web_search", {"q": "x"}, DO),
    ("gmail_create_draft", {"to": "a@b.c"}, DO),
    ("memory", {"action": "add"}, DO),
    ("terminal", {"command": "ls -la"}, DO),
    ("write_file", {"path": "notes.md", "content": "x"}, DO),
    # Tier 2: visible to others / changes schedule
    ("gmail_send_message", {"to": "a@b.c"}, ASK),
    ("gmail_reply_to_email", {}, ASK),
    ("calendar_create_event", {}, ASK),
    ("drive_share_file", {}, ASK),
    ("send_message", {"text": "hi"}, ASK),
    ("call_ben", {}, ASK),
    ("post_slack_message", {}, ASK),
    # Tier 2 via terminal content
    ("terminal", {"command": "curl -X POST https://api.x/y -d '{}'"}, ASK),
    ("terminal", {"command": "git push origin main"}, ASK),
    ("terminal", {"command": "osascript -e 'tell app \"Messages\"'"}, ASK),
    ("terminal", {"command": "mail -s hi a@b.c < body.txt"}, ASK),
    ("terminal", {"command": "echo x | sendmail -t"}, ASK),
    # The word "mail" inside Gmail queries / comments must NOT trip the send pattern (cron brief regression)
    ("terminal", {"command": "GAPI=\"${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-google/scripts/gapi\"; $GAPI gmail search \"is:unread newer_than:1d -category:promotions\" --max 20  # unread mail"}, DO),
    ("terminal", {"command": "$GAPI gmail search 'subject:mail merge' --max 5"}, DO),
    ("terminal", {"command": "cat ~/.hermes/jarvis-state/inbox.json  # held mail"}, DO),
    # Google skill via terminal: reads are tier 1, writes ask, permanent delete never
    ("terminal", {"command": "python ~/.hermes/skills/jarvis-google/scripts/google_api.py gmail search 'is:unread' --max 10"}, DO),
    ("terminal", {"command": "python ~/.hermes/skills/jarvis-google/scripts/google_api.py calendar list"}, DO),
    ("terminal", {"command": "python ~/.hermes/skills/jarvis-google/scripts/google_api.py drive download FILE --output ~/x.pdf"}, DO),
    ("terminal", {"command": "python ~/.hermes/skills/jarvis-google/scripts/google_api.py gmail send --to a@b.c --subject hi --body x"}, ASK),
    ("terminal", {"command": "$GAPI gmail reply MSG --body 'thanks'"}, ASK),
    ("terminal", {"command": "$GAPI calendar create --summary x --start 2026-10-09T10:00:00-04:00 --end 2026-10-09T11:00:00-04:00"}, ASK),
    ("terminal", {"command": "$GAPI drive share FILE --email a@b.c --role reader"}, ASK),
    ("terminal", {"command": "$GAPI drive delete FILE"}, ASK),
    ("terminal", {"command": "$GAPI drive delete FILE --permanent"}, NEVER),
    ("terminal", {"command": "~/.hermes/skills/jarvis-google/scripts/gapi gmail search 'is:unread'"}, DO),
    ("terminal", {"command": "~/.hermes/skills/jarvis-google/scripts/gapi gmail send --to a@b.c --subject x --body y"}, ASK),
    ("terminal", {"command": "GAPI=\"${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-google/scripts/gapi\"; $GAPI calendar create --summary x"}, ASK),
    # Phase 9: decks from a call. Building locally, uploading to Ben's own Drive and messaging Ben's Telegram are Tier 1;
    # emailing the deck to anyone stays Tier 2.
    ("terminal", {"command": "DECK=\"${HERMES_HOME:-$HOME/.hermes}/skills/jarvis-deck/scripts/deck\"; $DECK build /tmp/deck.json"}, DO),
    ("terminal", {"command": "~/.hermes/skills/jarvis-deck/scripts/deck example"}, DO),
    ("terminal", {"command": "hermes send -t telegram \"MEDIA:/Users/you/.hermes/jarvis-data/decks/x.pptx\""}, DO),
    ("terminal", {"command": "hermes send --to telegram -s Done 'Deck ready'"}, DO),
    ("terminal", {"command": "$GAPI drive upload ~/.hermes/jarvis-data/decks/x.pptx"}, DO),
    ("terminal", {"command": "$GAPI drive share FILE --email a@b.c"}, ASK),
    ("terminal", {"command": "$GAPI gmail send --to club@school.edu --subject deck --body hi --attach /x.pptx"}, ASK),
    # Tier 3: money, deletion, credentials
    ("stripe_create_payment", {}, NEVER),
    ("gmail_delete_message", {}, NEVER),
    ("drive_trash_file", {}, NEVER),
    ("bank_transfer", {}, NEVER),
    ("web_fill_form", {"fields": {"password": "x"}}, NEVER),
    ("browser_type", {"text": "x", "card_number": "4111"}, NEVER),
])
def test_classification(cfg, tool, args, tier):
    assert classify(tool, args, cfg["permissions"]).tier == tier


def test_unknown_tool_defaults_to_ask(cfg):
    d = classify("totally_new_tool", {}, cfg["permissions"])
    assert d.tier == ASK and d.rule == "default"


def test_override_wins(cfg):
    perms = {**cfg["permissions"], "overrides": {"gmail_send_message": 1}}
    assert classify("gmail_send_message", {}, perms).tier == DO


def test_tier3_beats_tier1_name(cfg):
    # A "read" tool that takes a password is still never.
    assert classify("read_vault", {"password": "x"}, cfg["permissions"]).tier == NEVER


def test_nested_credential_key(cfg):
    assert classify("browser_fill", {"form": [{"cvv": "123"}]}, cfg["permissions"]).tier == NEVER


def test_empty_permissions_defaults():
    assert classify("anything", {}, None).tier == ASK
