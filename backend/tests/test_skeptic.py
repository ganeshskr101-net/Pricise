"""Skeptic rules: pure functions, made-up findings, no network (meterless.deep is always mocked)."""
from datetime import date, timedelta

import pytest

from backend.agents import skeptic
from backend.agents.skeptic import (RuleResult, card_wording, challenge_reason, check_best_price, check_discount,
                                    check_evidence, check_timer, next_status, visits_from_frames)
from backend.contracts import Evidence, Finding
from backend.llm import meterless

EV = [Evidence(kind="screenshot", ref="/traces/x/visit1.png", label="Visit 1")]
FULL_MATCH = {"model": True, "variant": True, "condition": True, "warranty": True}
START = date(2026, 9, 1)


def make(type_="resetting_timer", status="proposed", evidence=None, rounds=0, summary="The timer restarts on every visit."):
    return Finding(check_id="c1", agent="deal", type=type_, status=status, summary=summary,
                   evidence=EV if evidence is None else evidence, rounds=rounds)


def visit(reset=True, fresh=True):
    return {"fresh_session": fresh, "reset": reset}


def history(days, price=100.0):
    return [(START + timedelta(days=i), price) for i in range(days)]


# Rule 1: evidence

def test_evidence_present_passes_and_keeps_status():
    assert check_evidence(make(status="confirmed")) == RuleResult(True, "confirmed", "1 evidence item(s) attached")


def test_no_evidence_rejected():
    r = check_evidence(make(evidence=[]))
    assert (r.passed, r.status) == (False, "rejected")


# Rule 2: timer

def test_timer_two_visits_unverified():
    r = check_timer(make(), [visit(), visit()])
    assert (r.passed, r.status) == (False, "unverified")


def test_timer_three_fresh_resets_passes():
    r = check_timer(make(), [visit(), visit(), visit()])
    assert (r.passed, r.status) == (True, "confirmed")


def test_timer_one_not_reset_fails():
    r = check_timer(make(), [visit(), visit(reset=False), visit()])
    assert (r.passed, r.status) == (False, "rejected")


def test_timer_visit_not_fresh_unverified():
    r = check_timer(make(), [visit(), visit(fresh=False), visit()])
    assert (r.passed, r.status) == (False, "unverified")


def frame(at, remaining):
    return {"visit": 1, "at": at, "time": "12:00:00", "shown": "09:58", "remaining_s": remaining, "screenshot": "/x.png"}


def test_frames_that_restart_convert_to_all_resets():
    visits = visits_from_frames([frame(0, 598), frame(3, 598), frame(6, 598)])
    assert visits == [visit(), visit(), visit()]
    assert check_timer(make(), visits).passed


def test_frames_that_count_down_convert_to_no_resets():
    visits = visits_from_frames([frame(0, 598), frame(3, 595), frame(6, 592)])
    assert [v["reset"] for v in visits] == [False, False, False]
    assert check_timer(make(), visits).status == "rejected"


def test_frames_without_readable_time_are_left_out():
    trimmed = [{"visit": 1, "time": "12:00:00", "shown": "09:58", "screenshot": "/x.png"}] * 3  # deal.EVIDENCE shape
    assert visits_from_frames(trimmed) == []
    assert check_timer(make(), visits_from_frames(trimmed)).status == "unverified"


# Rule 3: best price

def test_best_price_full_match_and_safe_passes():
    r = check_best_price(make("best_price"), FULL_MATCH, "caution")
    assert (r.passed, r.status) == (True, "confirmed")


def test_best_price_refurbished_rejected():
    r = check_best_price(make("best_price"), {**FULL_MATCH, "condition": False}, "safe")
    assert (r.passed, r.status) == (False, "rejected")
    assert "refurbished" in r.reason


def test_best_price_unsafe_store_rejected():
    r = check_best_price(make("best_price"), FULL_MATCH, "unsafe")
    assert r.status == "rejected" and "store flagged unsafe" in r.reason


def test_best_price_missing_key_and_unknown_verdict_rejected():
    r = check_best_price(make("best_price"), {"model": True}, None)
    assert r.status == "rejected"
    assert "different variant" in r.reason and "store safety not checked" in r.reason


# Rule 4: discount

def test_discount_29_days_before_sale_unverified():
    rows = history(29) + [(START + timedelta(days=29), 100.0)]  # latest row is the sale
    r = check_discount(make("fake_discount"), rows)
    assert (r.passed, r.status) == (False, "unverified")


def test_discount_default_sale_row_fake_confirmed():
    rows = history(30) + [(START + timedelta(days=30), 99.5)]  # within 1% of the normal 100
    r = check_discount(make("fake_discount"), rows)
    assert (r.passed, r.status) == (True, "confirmed")


def test_discount_default_sale_row_real_rejected():
    rows = history(30) + [(START + timedelta(days=30), 80.0)]
    r = check_discount(make("fake_discount"), rows)
    assert (r.passed, r.status) == (False, "rejected")


def test_discount_explicit_sale_price_fake_confirmed():
    r = check_discount(make("fake_discount"), history(30), sale_price=100.0)
    assert (r.passed, r.status) == (True, "confirmed")


def test_discount_explicit_sale_price_real_rejected():
    r = check_discount(make("fake_discount"), history(30), sale_price=98.0)  # just past the 1% tolerance
    assert (r.passed, r.status) == (False, "rejected")


def test_discount_explicit_sale_price_29_days_unverified():
    r = check_discount(make("fake_discount"), history(29), sale_price=100.0)
    assert r.status == "unverified"


def test_discount_uses_median_and_iso_dates():
    rows = [((START + timedelta(days=i)).isoformat(), 300.0 if i == 0 else 100.0) for i in range(30)]
    assert check_discount(make("fake_discount"), rows, sale_price=100.0).status == "confirmed"


def test_discount_empty_history_unverified():
    assert check_discount(make("fake_discount"), []).status == "unverified"


# Rule 5: next_status

def test_next_status_applies_result():
    out = next_status(make(), RuleResult(True, "confirmed", "ok"))
    assert (out.status, out.rounds, out.challenged_by) == ("confirmed", 1, "skeptic")


def test_third_challenge_unverified_and_rounds_capped():
    f = make()
    for expected in (1, 2):
        f = next_status(f, RuleResult(False, "rejected", "no"))
        assert (f.status, f.rounds) == ("rejected", expected)
    f = next_status(f, RuleResult(True, "confirmed", "ok"))
    assert (f.status, f.rounds, f.challenged_by) == ("unverified", 2, "skeptic")
    assert next_status(f, RuleResult(True, "confirmed", "ok")).rounds == 2


def test_next_status_does_not_mutate_input():
    f = make()
    before = f.model_dump()
    out = next_status(f, RuleResult(True, "confirmed", "ok"))
    assert f.model_dump() == before
    assert out is not f and out.evidence is not f.evidence


# Rule 6: card wording

def test_card_unverified_says_couldnt_verify_without_guess():
    text = card_wording(make(status="unverified", summary="Probably a fake timer"))
    assert "couldn't verify" in text and "Probably" not in text


def test_card_missing_evidence_says_couldnt_verify():
    assert "couldn't verify" in card_wording(make(status="confirmed", evidence=[]))


def test_card_confirmed_with_evidence_uses_summary():
    text = card_wording(make(status="confirmed"))
    assert text == "The timer restarts on every visit." and "couldn't verify" not in text


def test_card_rejected_and_proposed():
    assert card_wording(make(status="rejected")) == "Checked this countdown timer: no problem found."
    assert card_wording(make("odd_trick")) == "Still checking this odd trick."


# Optional wording helper

RESULT = RuleResult(False, "rejected", "timer kept counting down on 3 of 3 visits")


def test_challenge_reason_falls_back_on_failure(monkeypatch):
    def boom(*a, **kw):
        raise meterless.MeterlessError("down")

    monkeypatch.setattr(meterless, "deep", boom)
    assert challenge_reason(make(), RESULT) == RESULT.reason
    assert (RESULT.passed, RESULT.status) == (False, "rejected")


@pytest.mark.parametrize("reply", ["", "  ", "<think>only thinking</think>", "<think>cut off"])
def test_challenge_reason_falls_back_on_empty(monkeypatch, reply):
    monkeypatch.setattr(meterless, "deep", lambda *a, **kw: (reply, "nemotron-deep"))
    assert challenge_reason(make(), RESULT) == RESULT.reason


def test_challenge_reason_strips_think(monkeypatch):
    seen = {}

    def fake(messages, **kw):
        seen.update(kw)
        return "<think>hmm\nlet me see</think>\nThe timer kept counting down, so the deadline looks real.", "nemotron-deep"

    monkeypatch.setattr(meterless, "deep", fake)
    assert challenge_reason(make(), RESULT) == "The timer kept counting down, so the deadline looks real."
    assert seen["temperature"] == 0 and seen["timeout"] == skeptic._REASON_TIMEOUT_S
    assert (RESULT.passed, RESULT.status) == (False, "rejected")
