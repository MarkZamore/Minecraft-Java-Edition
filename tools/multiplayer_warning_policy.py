"""Carry the pack's native multiplayer-warning option across author imports."""

import argparse
import codecs
from io import StringIO
from pathlib import Path

RELATIVE_PATH = "launcher/options-default.txt"
KEY = "skipMultiplayerWarning"
REQUIRED_LINE = f"!{KEY}:true"


def matching_lines(lines):
    for index, line in enumerate(lines):
        entry = line.strip()
        if entry.startswith("!"):
            entry = entry[1:].lstrip()
        name, sep, _ = entry.partition(":")
        if sep and name.rstrip() == KEY:
            yield index


def verify_contents(data):
    lines = data.decode("utf-8-sig").split("\n")
    entries = [lines[index].strip() for index in matching_lines(lines)]
    if entries != [REQUIRED_LINE]:
        raise ValueError(f"{RELATIVE_PATH} must have exactly one {REQUIRED_LINE} entry; "
                         "run multiplayer_warning_policy.py prepare after importing upstream files")


def verify(pack):
    target = pack / RELATIVE_PATH
    verify_contents(target.read_bytes() if target.is_file() else b"")


def prepare(pack):
    target = pack / RELATIVE_PATH
    before = target.read_bytes() if target.exists() else b""
    bom = codecs.BOM_UTF8 if before.startswith(codecs.BOM_UTF8) else b""
    text = before.decode("utf-8-sig")
    lines = StringIO(text).readlines()
    matches = list(matching_lines(lines))
    if matches:
        first, *duplicates = matches
        body = lines[first].rstrip("\r\n")
        lines[first] = REQUIRED_LINE + lines[first][len(body):]
        for index in reversed(duplicates):
            del lines[index]
        updated = "".join(lines)
    else:
        newline = "\r\n" if "\r\n" in text else "\n"
        separator = newline if text and not text.endswith("\n") else ""
        updated = text + separator + REQUIRED_LINE + newline
    after = bom + updated.encode("utf-8")
    verify_contents(after)
    if after != before:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    if not args.pack.is_dir():
        parser.error(f"pack root does not exist: {args.pack}")
    try:
        if args.action == "prepare":
            prepare(args.pack)
        else:
            verify(args.pack)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
