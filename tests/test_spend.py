from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.spend import SpendMeter, Usage, estimate_usd, parse_usage

ET = ZoneInfo("America/New_York")


def test_parse_openai_and_anthropic_shapes():
    assert parse_usage({"prompt_tokens": 10, "completion_tokens": 5}) == Usage(10, 5, 0, 0)
    assert parse_usage({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3}) == Usage(10, 5, 3, 0)
    assert parse_usage(None) == Usage()


def test_estimate_matches_price_table(cfg):
    prices = cfg["spend"]["prices"]
    # 1M input + 1M output on Sonnet 5.5 = $2 + $10
    assert estimate_usd("anthropic/claude-sonnet-5-5", Usage(1_000_000, 1_000_000), prices) == 12.0
    assert estimate_usd("claude-haiku-5-5", Usage(1_000_000, 0), prices) == 0.10
    assert estimate_usd("unknown-model", Usage(1_000_000, 0), prices) == 0.0


def test_meter_levels(cfg):
    c = {**cfg, "spend": {**cfg["spend"], "daily_cap_usd": 1.0, "alert_fraction": 0.8}}
    m = SpendMeter(c)
    now = datetime(2026, 10, 8, 12, 0, tzinfo=ET)
    assert m.status(now)["level"] == "ok"
    m.record("claude-sonnet-5-5", {"prompt_tokens": 400_000, "completion_tokens": 0}, now)  # $0.80
    assert m.status(now)["level"] == "warn"
    m.record("claude-sonnet-5-5", {"prompt_tokens": 100_000, "completion_tokens": 0}, now)  # +$0.20 = $1.00
    assert m.status(now)["level"] == "over"
    # A new day resets.
    assert m.status(datetime(2026, 10, 9, 12, 0, tzinfo=ET))["level"] == "ok"
