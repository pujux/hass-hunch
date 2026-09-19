"""Guards for the exporter's read-only contract. Run with: uv run pytest tools/ -q"""

import asyncio
import pathlib
import re

import export_home as eh
import pytest

SRC = pathlib.Path(eh.__file__).read_text(encoding="utf-8")


def test_allowlist_contains_only_read_types():
    assert all(t.endswith("/list") or t == "get_states" for t in eh.READ_ONLY_TYPES)


def test_call_refuses_anything_not_allowlisted():
    class Sock:
        sent: list[str] = []

        async def send(self, m):
            self.sent.append(m)

        async def recv(self):
            raise AssertionError("must not be reached")

    sock = Sock()
    hass = eh.HassWS(sock)
    for forbidden in (
        "call_service",
        "config/entity_registry/update",
        "execute_script",
        "subscribe_events",
    ):
        with pytest.raises(eh.ReadOnlyViolation):
            asyncio.run(hass.call(forbidden))
    assert sock.sent == []


def test_source_never_mentions_write_apis():
    for needle in (
        "call_service",
        "/update",
        "/create",
        "/delete",
        "execute_script",
        "fire_event",
        "/api/services",
    ):
        # allowed only inside the allowlist comment that names them as refused
        hits = [m.start() for m in re.finditer(re.escape(needle), SRC)]
        for h in hits:
            line = SRC[:h].count("\n") + 1
            text = SRC.splitlines()[line - 1]
            assert text.lstrip().startswith("#"), (
                f"{needle!r} appears in code at line {line}: {text!r}"
            )


def test_engine_package_never_touches_home_assistant():
    src = pathlib.Path(eh.ROOT, "packages", "hunch", "src", "hunch")
    for f in src.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "HASS_" not in text and "websocket" not in text.lower(), f.name
