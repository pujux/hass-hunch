import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "hunch"


def test_hunch_never_imports_homeassistant():
    offenders = [p for p in SRC.rglob("*.py") if "homeassistant" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"HA imports found in engine library: {offenders}"
