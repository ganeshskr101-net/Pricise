"""bench/run_bench.py scoring: a tiny label fixture and fake check results. No browser, no network."""
import importlib.util
import json
from pathlib import Path

import pytest

from backend.contracts import Evidence, Finding

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_bench", ROOT / "bench" / "run_bench.py")
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)


def f(type_, status="confirmed", evidence=1, agent="deal", summary="s"):
    return {"agent": agent, "type": type_, "status": status, "evidence": evidence, "summary": summary}


def row(page, trick, expected):
    return {"page": page, "trick": trick, "expected": expected, "note": ""}


def card(safe="unknown"):
    return {"tone": "caution", "tricks": 1, "safe": safe}


@pytest.fixture
def outcomes():
    return [
        rb.page_outcome(row("deal/fd-01.html", "fake_discount", 1), [f("fake_discount")], card(), 1.0, 2.0),
        rb.page_outcome(row("deal/fd-02.html", "fake_discount", 1), [f("fake_discount", evidence=0)], card(), 1.0, 4.0),
        rb.page_outcome(row("deal/fd-06.html", "fake_discount", 0), [f("fake_discount", "rejected")], card(), 1.0, 1.0),
        rb.page_outcome(row("deal/tm-01.html", "resetting_timer", 1), [f("resetting_timer")], card(), 1.0, 12.0),
        rb.page_outcome(row("deal/tm-05.html", "resetting_timer", 0), [f("resetting_timer")], card(), 1.0, 12.0),
        rb.page_outcome(row("deal/ad-05.html", "prechecked_addon", 0),
                        [f("no_https", agent="trust", evidence=0), f("not_connected", "unverified", agent="trust"),
                         f("scarcity", "proposed")], card(), 1.0, 1.0),
        rb.page_outcome(row("clean/c-01.html", "clean_control", 0), [f("drip_fees")], card(), 1.0, 1.0),
        rb.page_outcome(row("trust/s-01.html", "scam_store", 1), [], card("bad"), 1.0, 1.0),
        rb.page_outcome(row("trust/s-02.html", "scam_store", 1), [], card("good"), 1.0, 1.0),
    ]


def test_load_labels_reads_expected_as_int(tmp_path):
    p = tmp_path / "labels.csv"
    p.write_text('page,trick,expected,note\ndeal/a.html,drip_fees,1,"fee, late"\ndeal/b.html,drip_fees,0,honest\n')
    assert rb.load_labels(p) == [row("deal/a.html", "drip_fees", 1) | {"note": "fee, late"},
                                 row("deal/b.html", "drip_fees", 0) | {"note": "honest"}]


def test_trick_detection_rate_counts_planted_pages_only(outcomes):
    res = rb.score(outcomes, "full")["per_type"]
    assert res["fake_discount"]["detection"] == {"k": 1, "n": 2, "rate": 0.5}
    assert res["resetting_timer"]["detection"] == {"k": 1, "n": 1, "rate": 1.0}
    assert res["prechecked_addon"]["detection"] == {"k": 0, "n": 0, "rate": None}  # no planted trick: no rate


def test_caught_needs_confirmed_and_evidence():
    r = row("deal/df-01.html", "drip_fees", 1)
    assert rb.page_outcome(r, [f("drip_fees")])["caught"]
    assert not rb.page_outcome(r, [f("drip_fees", evidence=0)])["caught"]
    missed = rb.page_outcome(r, [f("drip_fees", "unverified", summary="Couldn't check late checkout fees")])
    assert not missed["caught"] and missed["reason"] == "drip_fees unverified, evidence 1: Couldn't check late checkout fees"
    assert rb.page_outcome(r, [f("prechecked_addon")])["reason"] == "no drip_fees finding"


def test_false_alarms_count_confirmed_deal_types_on_clean_pages_only(outcomes):
    fa = rb.score(outcomes, "full")["false_alarms"]
    # clean: fd-06 (rejected, no), tm-05 (confirmed timer, yes), ad-05 (trust/scarcity only, no), c-01 (confirmed fees, yes)
    assert (fa["k"], fa["n"], fa["clean_pages"], fa["planned"], fa["left_out"]) == (2, 4, 4, 10, [])


def test_scam_detection_uses_card_safe_verdict_when_trust_is_connected(outcomes, monkeypatch):
    monkeypatch.setattr(rb, "NOT_CONNECTED", {rb.LOOKALIKE: "agent not connected"})
    scam = rb.score(outcomes, "full")["scam_detection"]
    assert (scam["k"], scam["n"], scam["status"]) == (1, 2, "scored")


def test_not_connected_types_are_na_with_page_count_never_zero(outcomes):
    res = rb.score(outcomes, "full")
    assert res["per_type"]["scam_store"] == {"status": "n/a", "reason": "agent not connected", "pages": 2, "planned": 7}
    assert res["scam_detection"]["status"] == "n/a" and "detection" not in res["per_type"]["scam_store"]
    assert res["exact_match"] == {"status": "n/a", "reason": "agent not connected", "pages": 0, "planned": 7}
    assert {n["type"] for n in res["na_types"]} == {"scam_store", "lookalike_listing"}


def test_sample_mode_makes_timer_and_fees_na_and_leaves_their_clean_pages_out(outcomes):
    res = rb.score(outcomes, "sample")
    assert res["per_type"]["resetting_timer"] == {"status": "n/a", "reason": "hidden browser off", "pages": 2, "planned": 6}
    assert res["per_type"]["drip_fees"]["status"] == "n/a"
    fa = res["false_alarms"]
    assert (fa["k"], fa["n"], fa["clean_pages"], fa["left_out"]) == (1, 3, 4, ["deal/tm-05.html"])


def test_sample_mode_flag_reaches_args_results_and_first_output_line(outcomes):
    assert rb.parse_args(["--sample"]).sample and not rb.parse_args([]).sample
    res = rb.build_results("sample", outcomes)
    assert res["mode"] == "sample"
    lines = rb.render(res).splitlines()
    assert lines[0].startswith("MODE: SAMPLE")
    assert lines[1].startswith("n/a: resetting_timer: n/a (hidden browser off), 2 pages")
    assert "4 clean pages, plan expected 10" in rb.render(res)
    tm01 = next(p for p in res["pages"] if p["page"] == "deal/tm-01.html")
    assert (tm01["scored_in"], tm01["excluded_reason"]) == (None, "n/a (hidden browser off)")
    assert "  deal/tm-01.html (resetting_timer, expected 1): n/a (hidden browser off)" in rb.render(res)


@pytest.mark.parametrize("mode", ["full", "sample"])
def test_every_labelled_page_is_scored_or_excluded(outcomes, mode):
    res = rb.build_results(mode, outcomes)
    acc = res["accounting"]
    assert acc["labelled"] == len(outcomes) == sum(acc[m] for m in rb.METRICS) + len(acc["excluded"])
    # the per-metric counts match the denominators actually used
    assert acc["trick_detection"] == sum(v["detection"]["n"] for t, v in res["per_type"].items()
                                         if t in rb.DEAL_TYPES and v["status"] == "scored")
    assert acc["false_alarms"] == res["false_alarms"]["n"]
    excluded = {x["page"]: x["reason"] for x in acc["excluded"]}
    want = {"trust/s-01.html": "n/a (agent not connected)", "trust/s-02.html": "n/a (agent not connected)"}
    if mode == "sample":
        want |= {"deal/tm-01.html": "n/a (hidden browser off)", "deal/tm-05.html": "n/a (hidden browser off)"}
    assert excluded == want
    assert f"Page accounting: 9 labelled = {acc['trick_detection']} in trick_detection" in rb.render(res)


def test_trick_rows_show_planted_and_honest_split(outcomes):
    res = rb.build_results("full", outcomes)
    assert (res["per_type"]["fake_discount"]["planted"], res["per_type"]["fake_discount"]["honest"]) == (2, 1)
    assert "3 (7): 2 planted, 1 honest" in rb.render(res)
    assert "Excluded pages: " in rb.render(res)
    assert rb.render(rb.build_results("full", outcomes)).startswith("MODE: FULL")


@pytest.mark.parametrize("values, want", [([3.0, 1.0, 2.0], 2.0), ([4.0, 1.0, 2.0, 3.0], 2.5), ([], None), ([None, 5.0], 5.0)])
def test_median_odd_even_empty(values, want):
    assert rb.median(values) == want


def test_timing_is_page_load_to_final_card(outcomes):
    tm = rb.score(outcomes, "full")["timing"]
    assert (tm["median_load_to_card_s"], tm["median_read_s"], tm["median_check_s"], tm["pages_timed"]) == (2.0, 1.0, 1.0, 9)
    assert rb.page_outcome(row("deal/x.html", "drip_fees", 1), [], error="TimeoutError")["load_to_card_s"] is None


def test_rate_is_cut_not_rounded_up():
    assert rb.fmt_rate(rb.rate(2, 3)) == "2/3 = 66.6%"
    assert rb.fmt_rate(rb.rate(5, 5)) == "5/5 = 100.0%"
    assert rb.fmt_rate(rb.rate(0, 0)) == "0/0 (no pages to score)"


def test_final_findings_keep_last_version_and_drop_placeholders():
    ev = [Evidence(kind="selector", ref="#timer")]
    stream = [Finding(check_id="c", agent="deal", type="drip_fees", status="proposed", summary="Checking…", model="pending"),
              Finding(check_id="c", agent="deal", type="resetting_timer", status="confirmed", summary="a", evidence=ev),
              Finding(check_id="c", agent="deal", type="resetting_timer", status="challenged", summary="a", evidence=ev),
              Finding(check_id="c", agent="deal", type="resetting_timer", status="rejected", summary="a", evidence=ev)]
    assert rb.final_findings(stream) == [{"agent": "deal", "type": "resetting_timer", "status": "rejected", "evidence": 1,
                                          "summary": "a"}]


def test_results_json_written_to_tmp_path_without_machine_paths(outcomes, tmp_path):
    res = rb.build_results("full", outcomes, {"TIMER_GAP_S": "3"}, date="2026-10-25T00:00:00+00:00")
    path = rb.write_results(res, tmp_path / "sub" / "results.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {"mode", "date", "per_type", "false_alarms", "exact_match", "scam_detection", "timing", "na_types", "pages",
            "notes", "accounting"} <= data.keys()
    assert data["pages"][0]["scored_in"] == "trick_detection" and data["pages"][0]["excluded_reason"] is None
    assert data["mode"] == "full" and data["notes"][0] == rb.CONFIRMED_NOTE
    page = data["pages"][0]
    assert page["findings"] == [{"agent": "deal", "type": "fake_discount", "status": "confirmed", "evidence": 1}]
    assert page["card"] == {"tone": "caution", "tricks": 1, "safe": "unknown"}
    assert str(ROOT) not in path.read_text(encoding="utf-8")
