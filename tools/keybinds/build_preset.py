#!/usr/bin/env python3
"""Generate the complete vanilla controls preset for the pinned client."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
# Keep vanilla muscle memory. Iris selection uses free J rather than F3+I's I.
# Add deliberate changes here rather than editing generated preset lines.
LAYOUT: dict[str, str] = {"iris.keybind.shaderPackSelection": "key.keyboard.j"}


def render() -> str:
    registry = json.loads((HERE / "registered-keys.json").read_text(encoding="utf-8"))
    known = {mapping["name"] for mapping in registry["mappings"]}
    if LAYOUT.keys() - known:
        raise ValueError(f"Unknown overrides: {LAYOUT.keys() - known}")
    lines = [f"# Minecraft {registry['minecraftVersion']} controls preset: standard vanilla controls.",
             "# Complete vanilla and Iris OpenGL mappings.",
             "# Vanilla spectator/debug overlaps are deliberate; see tools/keybinds/design.md.",
             "# Generated with python tools/keybinds/build_preset.py."]
    category = None
    for mapping in registry["mappings"]:
        if category != mapping["category"]:
            category = mapping["category"]
            lines.extend(["", f"# --- {category} ---"])
        value = LAYOUT.get(mapping["name"], mapping["default"])
        lines.append(f"key_{mapping['name']}:{value}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated preset without writing")
    args = parser.parse_args()
    expected = render()
    target = REPO / "launcher" / "controls-preset.txt"
    if args.check:
        if target.read_text(encoding="utf-8") != expected:
            raise SystemExit("Preset is out of date; run build_preset.py")
        print("Generated preset matches the complete registry")
    else:
        target.parent.mkdir(exist_ok=True)
        target.write_text(expected, encoding="utf-8", newline="\n")
        print("Wrote launcher/controls-preset.txt")


if __name__ == "__main__":
    main()
