"""Verify that the wheel in dist/ was built from the current engine source.

Run right before `uv publish`: a wheel built before the last source change would publish a
version that lacks it (that happened once with 0.4.0).

    uv run python tools/check_dist.py
"""

from __future__ import annotations

import pathlib
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "packages/hunch/src/hunch"


def main() -> int:
    version = tomllib.loads((ROOT / "packages/hunch/pyproject.toml").read_text())["project"][
        "version"
    ]
    wheel = ROOT / "dist" / f"hunch_engine-{version}-py3-none-any.whl"
    if not wheel.exists():
        print(f"no wheel for {version} in dist/ — run: uv build --package hunch-engine")
        return 1
    stale: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        packed = {
            n: zf.read(n) for n in zf.namelist() if n.startswith("hunch/") and n.endswith(".py")
        }
    for path in sorted(SRC.rglob("*.py")):
        name = "hunch/" + str(path.relative_to(SRC))
        if packed.get(name) != path.read_bytes():
            stale.append(name)
    extra = sorted(set(packed) - {"hunch/" + str(p.relative_to(SRC)) for p in SRC.rglob("*.py")})
    if stale or extra:
        for n in stale:
            print(f"differs from source: {n}")
        for n in extra:
            print(f"in wheel but not in source: {n}")
        print(f"wheel {wheel.name} is stale — rebuild before publishing")
        return 1
    print(f"{wheel.name} matches packages/hunch/src ({len(packed)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
