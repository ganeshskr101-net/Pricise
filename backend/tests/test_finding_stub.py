import json
 
import pytest
from pydantic import ValidationError
 
from backend._finding_stub import Finding
 
 
def make(**kw):
    base = dict(check_id="a91f", agent="deal", type="fake_timer", summary="Countdown resets")
    base.update(kw)
    return Finding(**base)
 
 
def test_defaults_and_json_roundtrip():
    f = make(evidence=[{"kind": "screenshot", "ref": "visit2.png"}], confidence=0.93)
    data = json.loads(f.model_dump_json())
    assert data["status"] == "proposed"
    assert data["evidence"][0]["ref"] == "visit2.png"
    assert Finding(**data) == f
 
 
def test_status_can_change():
    f = make()
    f.status = "confirmed"
    assert f.status == "confirmed"
 
 
def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        make(status="maybe")
 
 
def test_confidence_bounds():
    with pytest.raises(ValidationError):
        make(confidence=1.5)
 
