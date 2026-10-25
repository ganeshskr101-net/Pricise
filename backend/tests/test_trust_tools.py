"""Unit tests for each Trust Agent tool (pure rules, no network) plus store discovery."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.tools.domain import check_domain_age
from backend.tools.registry import check_registry
from backend.tools.returns import check_returns
from backend.tools.reviews import check_reviews
from backend.tools.trust_common import as_of_date, candidate_bases, sibling_stores

TODAY = date(2026, 10, 24)


# --- domain.py -----------------------------------------------------------------------------------------------------
def test_new_domain_is_flagged():
    s = check_domain_age({"domain_created": "2026-10-18"}, "u", TODAY)
    assert s.flagged and s.verified and s.strength == "strong" and "6 days" in s.reason


def test_old_domain_is_fine():
    s = check_domain_age({"domain_created": "2018-03-01"}, "u", TODAY)
    assert not s.flagged and s.verified and "8 years" in s.reason


def test_domain_30_days_is_not_new():
    assert not check_domain_age({"domain_created": (TODAY - timedelta(days=30)).isoformat()}, "u", TODAY).flagged


@pytest.mark.parametrize("about", [{}, {"domain_created": "soon"}])
def test_missing_domain_date_is_unverified_not_flagged(about):
    s = check_domain_age(about, "u", TODAY)
    assert not s.flagged and not s.verified


def test_store_lab_pins_today_with_as_of(monkeypatch):
    monkeypatch.delenv("TRUST_AS_OF", raising=False)
    assert as_of_date({"as_of": "2026-10-24"}) == TODAY
    monkeypatch.setenv("TRUST_AS_OF", "2026-11-01")
    assert as_of_date({"as_of": "2026-10-24"}) == date(2026, 11, 1)


# --- registry.py ---------------------------------------------------------------------------------------------------
def test_no_address_and_no_company_is_strong():
    s = check_registry({"address": "", "company_number": None}, "u")
    assert s.flagged and s.strength == "strong"


def test_placeholder_address_counts_as_missing():
    s = check_registry({"address": "n/a", "company_number": "VA-0412233"}, "u")
    assert s.flagged and s.strength == "weak"


def test_full_identity_is_fine():
    assert not check_registry({"address": "1 Main St, Fairfax", "company_number": "VA-0412233"}, "u").flagged


# --- reviews.py ----------------------------------------------------------------------------------------------------
def _revs(days_ago, texts=None):
    texts = texts or [f"honest review number {i}" for i in range(len(days_ago))]
    return [{"date": (TODAY - timedelta(days=d)).isoformat(), "text": t} for d, t in zip(days_ago, texts)]


def test_review_burst_is_flagged():
    burst, dup = check_reviews(_revs([1, 2, 2, 3, 3, 4, 4, 50, 90, 200]), "u")
    assert burst.flagged and not dup.flagged


def test_spread_out_reviews_are_fine():
    burst, dup = check_reviews(_revs([i * 20 for i in range(12)]), "u")
    assert not burst.flagged and not dup.flagged


def test_duplicate_reviews_are_flagged():
    texts = ["Amazing product, highly recommend!!"] * 4 + [f"honest review {i}" for i in range(8)]
    _, dup = check_reviews(_revs([i * 20 for i in range(12)], texts), "u")
    assert dup.flagged


def test_too_few_reviews_is_unverified():
    burst, dup = check_reviews(_revs([1, 1, 1]), "u")
    assert not burst.verified and not burst.flagged and not dup.flagged


def test_unreadable_reviews_is_unverified():
    burst, _ = check_reviews(None, "u")
    assert not burst.verified and "Couldn't" in burst.reason


# --- returns.py ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("page,flagged", [
    (None, True),
    ("<p>All sales are final.</p><p>Thank you for shopping with us.</p>", True),
    ("<p>Items can be returned within 3 days of delivery.</p>", True),
    ("<p>Return any unused item within 30 days for a refund.</p>", False),
    ("<p>We offer a 14-day return window on everything.</p>", False),
])
def test_returns_policy(page, flagged):
    assert check_returns(page, "u").flagged is flagged


# --- store discovery -----------------------------------------------------------------------------------------------
def test_candidate_bases_start_at_the_store():
    assert candidate_bases("http://localhost:8787/storelab/store-c/index.html")[:2] == [
        "http://localhost:8787/storelab/store-c", "http://localhost:8787/storelab"]


def test_sibling_stores_come_from_the_repo_folders():
    peers = sibling_stores("http://localhost:8787/storelab/store-d")
    assert "http://localhost:8787/storelab/store-c" in peers and "http://localhost:8787/storelab/audiodealz" in peers
    assert not any(p.endswith("store-d") or p.endswith("generic-shop") for p in peers)  # self, and folders without about.json
