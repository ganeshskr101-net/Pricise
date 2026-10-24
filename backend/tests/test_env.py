"""The .env loader: shell variables win, empty values are skipped, and no .env at all is fine."""
from backend.env import load_dotenv


def test_dotenv_fills_only_missing_non_empty_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nPRICISE_T_A=from-file\nPRICISE_T_B=from-file\nPRICISE_T_EMPTY=\nPRICISE_T_C=\"quoted\"  # note\n")
    monkeypatch.setenv("PRICISE_T_B", "from-shell")
    for k in ("PRICISE_T_A", "PRICISE_T_EMPTY", "PRICISE_T_C"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(env)
    import os
    assert os.environ["PRICISE_T_A"] == "from-file"
    assert os.environ["PRICISE_T_B"] == "from-shell"
    assert "PRICISE_T_EMPTY" not in os.environ
    assert os.environ["PRICISE_T_C"] == "quoted"
    for k in ("PRICISE_T_A", "PRICISE_T_C"):
        monkeypatch.delenv(k)


def test_missing_dotenv_is_fine(tmp_path):
    load_dotenv(tmp_path / "nope.env")
