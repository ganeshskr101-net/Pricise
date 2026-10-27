"""Nemotron wiring without live calls: reasoning stripped, reasoning off by default, the Skeptic's deep-lane wording
(orchestrator.explain) never changes a decision, and the chat never shows its SOURCES line to the shopper."""
import asyncio

import pytest

from backend import orchestrator
from backend.agents import chat, skeptic
from backend.agents.skeptic import RuleResult
from backend.contracts import Evidence, Finding
from backend.llm import meterless

DEEP = "nvidia/nemotron-3-ultra-550b-a55b"


# ----------------------------------------------------------------------------- strip_reasoning


@pytest.mark.parametrize("raw, want", [
    ("OK", "OK"),
    ("<think>plan it</think>OK", "OK"),
    ("The user wants one word.</think>OK", "OK"),                # reasoning with no opening tag (deep lane)
    ("<think>a</think> first </think> second", "second"),
    ("<think>never finished", ""),                               # cut off mid-reasoning: no answer
    ("", ""),
])
def test_strip_reasoning(raw, want):
    assert meterless.strip_reasoning(raw) == want


def test_skeptic_reply_cleaning_drops_bare_closing_tag():
    assert skeptic._clean_reply("Reasoning here.</think> Rejected:  refurbished. ") == "Rejected: refurbished."


# ----------------------------------------------------------------------------- the request


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """Capture what would be POSTed; nothing leaves the machine, and the real traces/llm_calls.jsonl stays untouched."""
    out = {}
    monkeypatch.setattr(meterless, "_trace_path", lambda: tmp_path / "llm_calls.jsonl")
    monkeypatch.setattr(meterless, "_settings", lambda: {"base_url": "http://127.0.0.1:1/v1", "token": "t",
                                                         "fast_model": "fast-m", "deep_model": DEEP})

    def post(url, headers, payload, token, timeout):
        out.update(payload=payload, timeout=timeout)
        return {"choices": [{"message": {"content": "Thinking.</think>Fine."}}], "model": DEEP}, DEEP, 1, 1

    monkeypatch.setattr(meterless, "_post_with_retry", post)
    monkeypatch.setattr(meterless, "configured", lambda lane="fast": True)
    return out


def test_reasoning_is_off_by_default_and_can_be_overridden(sent):
    meterless.fast([{"role": "user", "content": "hi"}])
    assert sent["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    meterless.deep([{"role": "user", "content": "hi"}], chat_template_kwargs={"enable_thinking": True})
    assert sent["payload"]["chat_template_kwargs"] == {"enable_thinking": True}


def test_complete_strips_reasoning_and_takes_a_timeout(sent):
    assert meterless.complete("hi", "deep") == "Fine."
    assert sent["timeout"] == 8.0
    meterless.complete("hi", "fast", timeout=15)
    assert sent["timeout"] == 15


# ----------------------------------------------------------------------------- the Skeptic's wording (explain)

RESULT = RuleResult(False, "rejected", "refurbished")


def _bp(summary="Store B, $199: cheaper look-alike (refurbished)."):
    return Finding(check_id="c1", agent="price", type="best_price", status="proposed", summary=summary,
                   evidence=[Evidence(kind="url", ref="http://localhost/storelab/store-b/")], model="rules (no model)")


def _explain(before):
    return asyncio.run(orchestrator.explain(before, RESULT, skeptic.next_status(before, RESULT)))


def test_explain_without_nightshift_changes_nothing(monkeypatch):
    monkeypatch.setattr(meterless, "configured", lambda lane="fast": False)
    called = []
    monkeypatch.setattr(skeptic, "challenge_reason", lambda f, r: called.append(1) or "worded")
    out = _explain(_bp())
    assert out == skeptic.next_status(_bp(), RESULT) and not called


def test_explain_words_the_reason_and_names_the_model_but_keeps_the_decision(monkeypatch):
    monkeypatch.setattr(meterless, "configured", lambda lane="fast": True)
    monkeypatch.setattr(meterless, "model_name", lambda lane="fast": DEEP)
    monkeypatch.setattr(skeptic, "challenge_reason", lambda f, r: "This offer is refurbished, not new.")
    out = _explain(_bp())
    assert (out.status, out.challenged_by, out.rounds) == ("rejected", "skeptic", 1)
    assert out.model == DEEP
    assert out.summary == "Store B, $199: cheaper look-alike (refurbished). Skeptic: This offer is refurbished, not new."
    again = asyncio.run(orchestrator.explain(out, RESULT, skeptic.next_status(out, RESULT)))
    assert again.summary.count("Skeptic:") == 1  # a second round replaces the wording, never stacks it


def test_explain_falls_back_to_the_rule_reason_without_naming_a_model(monkeypatch):
    monkeypatch.setattr(meterless, "configured", lambda lane="fast": True)
    monkeypatch.setattr(skeptic, "challenge_reason", lambda f, r: r.reason)  # what it returns when the model failed
    out = _explain(_bp())
    assert out.model == "rules (no model)" and out.summary.endswith("Skeptic: refurbished.")
    assert out.status == "rejected"


# ----------------------------------------------------------------------------- chat: SOURCES never shown

CHECK = {"findings": [{"type": "drip_fees", "summary": "$37.01 in fees at checkout. Real total: $191.00."},
                      {"type": "prechecked_addon", "summary": "A $4.99 add-on is ticked."}, {"type": "fake_discount", "summary": ""}]}


@pytest.mark.parametrize("reply", [
    "The real total is $191.00.\nSOURCES: drip_fees, prechecked_addon",
    "The real total is $191.00. SOURCES: drip_fees, prechecked_addon",          # same line (seen live)
    "The real total is $191.00.\n\n**Sources:** `drip_fees`, `prechecked_addon`",
    "The real total is $191.00. sources: drip_fees, prechecked_addon.",
])
def test_sources_are_tags_never_text(reply):
    res = chat._parse_model(reply, CHECK)
    assert res.answer == "The real total is $191.00."
    assert res.sources == ["drip_fees", "prechecked_addon"]
    assert "source" not in res.answer.lower()


def test_answer_uses_the_model_with_a_15_s_limit_and_hides_sources(monkeypatch):
    seen = {}

    def fake_complete(prompt, lane="fast", max_tokens=64, timeout=None):
        seen.update(lane=lane, timeout=timeout)
        return "No, the 40% off is not real. SOURCES: fake_discount"

    monkeypatch.setattr(meterless, "complete", fake_complete)
    res = chat.answer({**CHECK, "page": {}}, "Is the discount real?")
    assert res.answer == "No, the 40% off is not real." and res.sources == ["fake_discount"]
    assert seen == {"lane": "fast", "timeout": 15.0}
