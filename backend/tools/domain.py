"""Domain age check. Store Lab stores are made up, so the 'WHOIS' creation date comes from about.json (domain_created)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from backend.tools.trust_common import Signal, as_of_date

NEW_DOMAIN_DAYS = 30


def check_domain_age(about: dict, about_url: str, today: Optional[date] = None) -> Signal:
    today = today or as_of_date(about)
    raw = about.get("domain_created")
    ev = [{"kind": "url", "ref": about_url}]
    try:
        created = date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        created = None
    if created is None:
        return Signal("domain_age", "strong", False, False, "Couldn't check how old the domain is (no registration date).",
                      ev, 0.3)
    age = (today - created).days
    ev.append({"kind": "log", "ref": f"domain_created={created.isoformat()} as_of={today.isoformat()} age_days={age}"})
    if age < NEW_DOMAIN_DAYS:
        n = max(age, 0)
        return Signal("domain_age", "strong", True, True, f"Domain registered {n} day{'' if n == 1 else 's'} ago.", ev, 0.95)
    years = age // 365
    told = f"{years} year{'s' if years != 1 else ''}" if years else f"{age} days"
    return Signal("domain_age", "strong", False, True, f"Domain registered {told} ago.", ev, 0.9)
