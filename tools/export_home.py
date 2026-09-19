"""Export a Home Assistant installation's registries into golden/homes/<name>.json.

Read-only: lists floors, areas, devices, entities, the Assist-exposed set and current states
over the WebSocket API. Nothing is changed in Home Assistant.

Run:   uv run python tools/export_home.py --name myhome
Needs HASS_URL (http(s)://host[:port]) and HASS_TOKEN (long-lived access token) in .env.
The output directory golden/homes/ is gitignored: an entity list is personal data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any

import websockets
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]


# The ONLY message types this tool is allowed to send. Every one is a read. Anything else —
# call_service, config/*/update, execute_script, ... — is refused before it reaches the socket.
READ_ONLY_TYPES = frozenset(
    {
        "config/floor_registry/list",
        "config/area_registry/list",
        "config/device_registry/list",
        "config/entity_registry/list",
        "homeassistant/expose_entity/list",
        "get_states",
    }
)


class ReadOnlyViolation(RuntimeError):
    """Raised when something tries to send a non-read message type."""


class HassWS:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 1

    async def call(self, msg_type: str, **kwargs: Any) -> Any:
        if msg_type not in READ_ONLY_TYPES:
            raise ReadOnlyViolation(f"refusing to send non-read message type {msg_type!r}")
        msg_id = self._next_id
        self._next_id += 1
        await self._ws.send(json.dumps({"id": msg_id, "type": msg_type, **kwargs}))
        while True:
            reply = json.loads(await self._ws.recv())
            if reply.get("id") != msg_id:
                continue  # unrelated event
            if not reply.get("success", False):
                raise RuntimeError(f"{msg_type} failed: {reply.get('error')}")
            return reply["result"]


async def export(url: str, token: str) -> dict[str, Any]:
    ws_url = (
        url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    )
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {hello.get('type')}")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError("authentication failed (check HASS_TOKEN)")
        hass = HassWS(ws)

        floors = await hass.call("config/floor_registry/list")
        areas = await hass.call("config/area_registry/list")
        devices = await hass.call("config/device_registry/list")
        entities = await hass.call("config/entity_registry/list")
        exposed_raw = await hass.call("homeassistant/expose_entity/list")
        states_raw = await hass.call("get_states")

    exposed = sorted(
        eid
        for eid, assistants in exposed_raw.get("exposed_entities", {}).items()
        if assistants.get("conversation")
    )
    states = {
        s["entity_id"]: {
            "state": s.get("state"),
            "friendly_name": s.get("attributes", {}).get("friendly_name"),
        }
        for s in states_raw
    }
    return {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ha_version": auth.get("ha_version"),
        "floors": [
            {
                "floor_id": f["floor_id"],
                "name": f["name"],
                "aliases": f.get("aliases", []),
                "level": f.get("level"),
            }
            for f in floors
        ],
        "areas": [
            {
                "area_id": a["area_id"],
                "name": a["name"],
                "aliases": a.get("aliases", []),
                "floor_id": a.get("floor_id"),
            }
            for a in areas
        ],
        "devices": [
            {
                "id": d["id"],
                "name": d.get("name"),
                "name_by_user": d.get("name_by_user"),
                "area_id": d.get("area_id"),
            }
            for d in devices
        ],
        "entities": [
            {
                "entity_id": e["entity_id"],
                "name": e.get("name"),
                "original_name": e.get("original_name"),
                "aliases": e.get("aliases", []),
                "area_id": e.get("area_id"),
                "device_id": e.get("device_id"),
            }
            for e in entities
        ],
        "exposed": exposed,
        "states": states,
    }


def summarize(data: dict[str, Any]) -> str:
    from collections import Counter

    exposed = data["exposed"]
    by_domain = Counter(eid.split(".", 1)[0] for eid in exposed)
    lines = [
        f"HA {data.get('ha_version')} — {len(data['floors'])} floors, {len(data['areas'])} areas, "
        f"{len(data['devices'])} devices, {len(data['entities'])} registry entities, "
        f"{len(exposed)} exposed to Assist",
        "exposed by domain: " + ", ".join(f"{d}={n}" for d, n in by_domain.most_common()),
    ]
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="output file stem under golden/homes/")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    url, token = os.environ.get("HASS_URL"), os.environ.get("HASS_TOKEN")
    if not url or not token:
        print("HASS_URL and HASS_TOKEN must be set in .env", file=sys.stderr)
        return 2
    data = await export(url, token)
    out = ROOT / "golden" / "homes" / f"{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"wrote {out.relative_to(ROOT)}")
    print(summarize(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
