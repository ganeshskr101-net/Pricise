"""Business identity check: is there a business address and a company record? Read from the store's about.json."""
from __future__ import annotations

from backend.tools.trust_common import Signal

_PLACEHOLDERS = {"", "n/a", "na", "none", "null", "-", "tbd", "unknown"}


def _present(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() not in _PLACEHOLDERS and len(value.strip()) >= 4


def check_registry(about: dict, about_url: str) -> Signal:
    has_address, has_company = _present(about.get("address")), _present(about.get("company_number"))
    ev = [{"kind": "url", "ref": about_url},
          {"kind": "log", "ref": f"address_present={has_address} company_number_present={has_company}"}]
    if not has_address and not has_company:
        return Signal("no_company", "strong", True, True, "No address or company record found for this store.", ev, 0.9)
    if not has_address:
        return Signal("no_company", "weak", True, True, "No business address listed.", ev, 0.8)
    if not has_company:
        return Signal("no_company", "weak", True, True, "No company registration number listed.", ev, 0.8)
    return Signal("no_company", "strong", False, True, "Business address and company record listed.", ev, 0.9)
