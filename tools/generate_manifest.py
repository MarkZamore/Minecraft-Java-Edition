#!/usr/bin/env python3
"""Generate the LL8 pack distribution manifest and staged release assets.

Usage:
    python tools/generate_manifest.py --pack <packRoot> --out <distDir> [--revision <rev>]
    python tools/generate_manifest.py --pack <packRoot> --out <distDir> --verify-only

Behavior:
    * Collects the shipped file set: everything under the roots
      mods/, config/, kubejs/, scripts/, defaultconfigs/, data/, resourcepacks/,
      shaderpacks/, configureddefaults/, launcher/ (recursively, absent roots
      are skipped) plus the root files portable-pack.json and the client jar
      named by portable-pack.json's "clientJar" field. launcher/ carries data
      the launcher reads (the controls preset), not the game.
    * Stages release assets into <distDir>/assets/:
        - every file under mods/ becomes its own byte-for-byte asset (kind "jar");
        - every other non-empty top-level root becomes ONE deterministic zip
          (kind "zipRoot") that is byte-stable across runs;
        - portable-pack.json and the client jar become individual assets.
    * Writes <distDir>/pack-manifest.json after validating that every shipped
      file is covered by exactly one asset and all asset names are unique.
    * --verify-only re-hashes 5 random staged assets against the manifest
      (CI self-check) instead of generating anything.

Python 3.12, standard library only.
"""

from __future__ import annotations

from pack_library import read_mods, read_pack_mods, stage_library, verify_library
import network_logging_policy

import argparse
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import sys
import pathlib
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCAN_ROOTS = ("mods", "config", "kubejs", "scripts", "defaultconfigs", "data",
              "resourcepacks", "shaderpacks", "configureddefaults", "launcher")
ZIP_ROOTS = ("config", "kubejs", "scripts", "defaultconfigs", "data",
             "resourcepacks", "shaderpacks", "configureddefaults", "launcher")
# Launcher-local marker/state files that must never ship inside the pack.
EXCLUDED_BASENAMES = {".portable-pack-sync.json", "portable-pack-source.json"}
EXCLUDED_TOP_DIRS = {"tools", "dist"}
# Fixed DOS epoch => identical zip bytes across runs.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
ZIP_COMPRESSLEVEL = 9
CHUNK = 1024 * 1024
VERIFY_SAMPLE = 10
PAUSE_ACTIONS_CONTRACT = {
    "schemaVersion": 1,
    "policyId": "lanmc.pause-actions",
    "modId": "lanmc_pause_actions",
    "modVersion": "1.0.2",
    "artifactPath": "mods/lanmc-pause-actions-1.0.2.jar",
    "sourceProject": "MarkZamore/Minecraft-26.3",
    "projectUrl": "https://github.com/MarkZamore/Minecraft-26.3/tree/main/tools/pause-actions",
    "screen": "net.minecraft.client.gui.screens.PauseScreen",
    "translationKey": "options.worldOptions.button",
    "lanTranslationKey": "menu.multiplayerOptions.button",
    "reportsTranslationKey": "menu.playerReporting",
    "replaceInPlace": True,
    "clientOnly": True,
    "requiredJvmProperties": {"minecraft.portable.identity.pauseLanRowEnabled": "false"},
    "steamDiscovery": {
        "publisherClass": "minecraft.portable.identity.PortableSteamDiscoveryHooks",
        "publisherMethod": "published",
        "publisherDescriptor": "(Ljava/lang/Object;Ljava/lang/Object;I)V",
        "notifyAfterSuccessfulNativePublish": True,
        "optionalAtRuntime": True,
        "preserveNativePublishOnFailure": True,
        "transportVersionIndependent": True,
    },
}
PAUSE_ACTIONS_EMBEDDED_CONTRACT = "META-INF/lanmc-pause-actions.json"
PAUSE_ACTIONS_MIXINS = "lanmc-pause-actions.mixins.json"

# mods/ is shipped as zip chunks so the GitHub release stays far below its
# 1000-asset ceiling with an 800+ mod pack. The bucket of a jar is a pure
# function of its relative path, so adding or updating one mod re-chunks (and
# re-downloads) only the bucket it lives in (~6.5 MB per chunk at 256).
# FROZEN since the first published LL8 release: changing the count reshuffles
# every bucket and forces every client to re-download the whole mods/ tree.
MODS_CHUNK_COUNT = 256
# Frequently-updated jars (launcher pins, pinned mods, quest/render core) stay
# single-file assets so their updates never drag a whole chunk along.
HOT_MODS_FILE = Path(__file__).resolve().parent / "hot-mods.json"


def fail(message: str) -> "NoReturn":  # noqa: F821 - typing comment only
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_name(name: str) -> str:
    """Keep [A-Za-z0-9._-], replace everything else with '-', collapse runs, cap at 80."""
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    out = re.sub(r"--+", "-", out)
    return out[:80] or "asset"


def load_file_lock(pack: Path, name: str) -> list[dict]:
    path = pack / "tools" / name
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or not isinstance(document.get("files"), list):
        fail(f"invalid {name}")
    found = set()
    for row in document["files"]:
        rel = row.get("path", "")
        if (not isinstance(rel, str) or not rel.startswith("mods/") or "\\" in rel or
                any(part in ("", ".", "..") for part in rel.split("/")) or
                ":" in rel or not rel.lower().endswith(".jar") or rel.casefold() in found):
            fail(f"invalid/duplicate path in {name}: {rel}")
        found.add(rel.casefold())
        if (not isinstance(row.get("sizeBytes"), int) or row["sizeBytes"] <= 0 or
                not re.fullmatch("[a-f0-9]{64}", row.get("sha256", ""))):
            fail(f"invalid size/SHA-256 in {name}: {rel}")
    return document["files"]


def validate_mod_lock(pack: Path, portable: dict, shipped: list[str], file_meta: dict) -> None:
    path = pack / "tools/distribution-lock.json"
    if not path.is_file():
        return
    document = json.loads(path.read_text(encoding="utf-8"))
    locked = load_file_lock(pack, "distribution-lock.json")
    if (document.get("minecraftVersion") != portable.get("minecraftVersion") or
            document.get("loader") != portable.get("loader")):
        fail("distribution lock does not match Minecraft/loader")
    expected = {row["path"]: (row["sha256"], row["sizeBytes"]) for row in locked}
    actual = {rel: file_meta[rel] for rel in shipped if rel.startswith("mods/")}
    if expected != actual:
        fail("active mod files differ from the exact-hash distribution lock")
    for row in locked:
        ids = sorted({mod["id"] for mod in read_mods(
            (pack / row["path"]).read_bytes(), row["path"], loader=portable["loader"]["type"].lower())
            if mod["isLoadedMod"]})
        if sorted(row.get("modIds", [])) != ids:
            fail("active mod IDs differ from the distribution lock: " + row["path"])


def validate_pause_actions(pack: Path, portable: dict, shipped: list[str]) -> None:
    """Require the pack-owned same-button policy independently of its mod lock.

    Updating a runtime descriptor, or deleting both a JAR and its lock entry,
    must not silently restore the world-settings entry or the extra LAN row,
    or lose the successful native publication's Steam discovery notification.
    This contract intentionally contains no Minecraft/e4steam version branch.
    Generic generator fixtures without this source contract remain generic;
    this pack's CI also unconditionally builds/probes the source component.
    """
    source = pack / "tools/pause-actions/contract.json"
    if not source.is_file():
        return

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate pause-actions field: " + key)
            result[key] = value
        return result

    def read_json(data):
        if len(data) > 65536:
            raise ValueError("pause-actions metadata is too large")
        return json.loads(data, object_pairs_hook=unique_object)

    def same_typed_value(value, expected):
        # JSON's true and 1 compare equal in Python. Require exact types through
        # nested integration policy fields as well as the top-level contract.
        if type(value) is not type(expected):
            return False
        if isinstance(expected, dict):
            return (value.keys() == expected.keys() and
                    all(same_typed_value(value[key], item) for key, item in expected.items()))
        return value == expected

    def read_contract(data):
        contract = read_json(data)
        if not same_typed_value(contract, PAUSE_ACTIONS_CONTRACT):
            raise ValueError("pause-actions contract or source project identity differs")
        return contract

    try:
        if source.stat().st_size > 65536:
            raise ValueError("pause-actions source contract is too large")
        contract = read_contract(source.read_bytes())
        rel = contract["artifactPath"]
        if rel not in shipped or not (pack / rel).is_file():
            fail("required pause-actions component is missing: " + rel)
        loader = portable.get("loader")
        if not isinstance(loader, dict) or loader.get("type") != "neoforge":
            fail("pause-actions requires its verified NeoForge client implementation")

        jvm_rel = "launcher/jvm-args.txt"
        if jvm_rel not in shipped or not (pack / jvm_rel).is_file():
            fail("pause-actions requires the pack's launcher/jvm-args.txt")
        required = contract["requiredJvmProperties"]
        counts = dict.fromkeys(required, 0)
        for raw in (pack / jvm_rel).read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line.startswith("-D"):
                continue
            key, separator, value = line[2:].partition("=")
            if key in required:
                counts[key] += 1
                if not separator or value != required[key]:
                    fail("pause-actions requires -D" + key + "=" + required[key])
        if any(count != 1 for count in counts.values()):
            fail("pause-actions requires exactly one declaration of each required JVM property")

        for active in shipped:
            if not active.startswith("mods/") or not active.lower().endswith(".jar"):
                continue
            if Path(active).name.casefold().startswith("lanmc-world-menu-") or any(
                    mod["id"] == "lanmc_world_menu" and mod["isLoadedMod"]
                    for mod in read_mods((pack / active).read_bytes(), active, loader="neoforge")):
                fail("retired world-menu helper must not be active: " + active)

        with zipfile.ZipFile(pack / rel) as archive:
            names = ("META-INF/neoforge.mods.toml", PAUSE_ACTIONS_EMBEDDED_CONTRACT,
                     PAUSE_ACTIONS_MIXINS, "lanmc/pauseactions/mixin/ButtonActionAccessor.class",
                     "lanmc/pauseactions/PauseActions.class",
                     "lanmc/pauseactions/SteamWorldDiscovery.class")
            for name in names:
                entries = [entry for entry in archive.infolist()
                           if entry.filename.casefold() == name.casefold()]
                if (len(entries) != 1 or entries[0].filename != name or
                        entries[0].file_size == 0 or entries[0].file_size > 65536):
                    fail("pause-actions requires one exact component entry: " + name)
            if read_contract(archive.read(PAUSE_ACTIONS_EMBEDDED_CONTRACT)) != contract:
                fail("pause-actions JAR does not embed its source contract")
            metadata = tomllib.loads(archive.read(names[0]).decode("utf-8-sig"))
            mods = metadata.get("mods")
            if (metadata.get("modLoader") != "javafml" or not isinstance(mods, list) or
                    len(mods) != 1 or not isinstance(mods[0], dict) or
                    mods[0].get("modId") != contract["modId"] or
                    mods[0].get("version") != contract["modVersion"] or
                    mods[0].get("displayURL") != contract["projectUrl"] or
                    metadata.get("mixins") != [{"config": PAUSE_ACTIONS_MIXINS}]):
                fail("pause-actions JAR root identity or required mixin registration differs")
            mixins = read_json(archive.read(PAUSE_ACTIONS_MIXINS))
            injectors = mixins.get("injectors") if isinstance(mixins, dict) else None
            if (not isinstance(mixins, dict) or mixins.get("required") is not True or
                    mixins.get("package") != "lanmc.pauseactions.mixin" or
                    mixins.get("client") != ["ButtonActionAccessor"] or
                    mixins.get("mixins", []) != [] or mixins.get("server", []) != [] or
                    not isinstance(injectors, dict) or type(injectors.get("defaultRequire")) is not int or
                    injectors["defaultRequire"] != 1):
                fail("pause-actions requires its mandatory client ButtonActionAccessor mixin")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        fail("cannot validate required pause-actions: " + str(exc))


def load_legacy_files(pack: Path, shipped: list[str]) -> list[dict]:
    active = {rel.casefold() for rel in shipped}
    legacy = load_file_lock(pack, "legacy-files.json")
    for row in legacy:
        if row["path"].casefold() in active:
            fail("legacy file must not also be active: " + row["path"])
    return [{key: row[key] for key in ("path", "sizeBytes", "sha256")} for row in legacy]


def is_excluded(parts: tuple[str, ...]) -> bool:
    if parts and parts[0] in EXCLUDED_TOP_DIRS:
        return True
    for part in parts:
        if part.startswith(".git"):
            return True
        if part in EXCLUDED_BASENAMES:
            return True
    return False


def walk_root(pack: Path, root: str) -> list[str]:
    """Forward-slash, case-exact relative paths of all shipped files under <pack>/<root>."""
    base = pack / root
    if not base.is_dir():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for filename in sorted(filenames):
            rel_parts = (Path(dirpath) / filename).relative_to(pack).parts
            if is_excluded(rel_parts):
                continue
            found.append("/".join(rel_parts))
    return found


def load_portable_pack(pack: Path) -> dict:
    pp_path = pack / "portable-pack.json"
    if not pp_path.is_file():
        fail(f"portable-pack.json not found in pack root: {pack}")
    try:
        data = json.loads(pp_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        fail(f"cannot parse {pp_path}: {exc}")
    if not isinstance(data, dict):
        fail("portable-pack.json must contain a JSON object")
    # A pack may bring its own client jar or leave the field out, in which case
    # the launcher fetches the official client from Mojang for the version the
    # manifest names. A vanilla build has no reason to carry a second copy of a
    # jar every player can already be handed, so absent is allowed and empty
    # means the same as absent; anything else that is not a jar name is a
    # mistake worth stopping for.
    client_jar = data.get("clientJar")
    if client_jar is None or client_jar == "":
        data["clientJar"] = ""
        return data
    if not isinstance(client_jar, str):
        fail("portable-pack.json's 'clientJar' must be a string naming a jar in the pack root")
    return data


class Staging:
    """Accumulates staged assets, deduplicating byte-identical single-file assets."""

    def __init__(self, assets_dir: Path) -> None:
        self.assets_dir = assets_dir
        self.by_name: dict[str, dict] = {}

    def add_single_file(self, pack: Path, rel: str, prefix: str,
                        sha256: str, size_bytes: int) -> None:
        src = pack / Path(rel)
        name = f"{prefix}-{sha256[:12]}-{sanitize_name(src.name)}"
        existing = self.by_name.get(name)
        if existing is not None:
            # The launcher rejects jar assets covering more than one path, so
            # byte-identical twins must not be collapsed into one asset.
            fail(f"two shipped files map to the same single-file asset: {name} "
                 f"({existing['covers'][0]} vs {rel})")
        shutil.copyfile(src, self.assets_dir / name)
        self.by_name[name] = {
            "name": name,
            "kind": "jar",
            "sha256": sha256,
            "sizeBytes": size_bytes,
            "covers": [rel],
        }

    def add_zip_root(self, pack: Path, root: str, rel_files: list[str],
                     compress: bool = True) -> None:
        self._add_zip(pack, root + ".zip", rel_files, compress)

    def add_mods_chunk(self, pack: Path, bucket: int, rel_files: list[str]) -> None:
        # Jars are already deflate-compressed; store them to keep CI fast and
        # chunk sizes predictable. Determinism is unaffected.
        self._add_zip(pack, f"mods.c{bucket:03}.zip", rel_files, compress=False)

    def _add_zip(self, pack: Path, label: str, rel_files: list[str],
                 compress: bool) -> None:
        method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        tmp = self.assets_dir / f".tmp-{label}"
        with zipfile.ZipFile(tmp, "w", compression=method,
                             compresslevel=ZIP_COMPRESSLEVEL if compress else None) as archive:
            for rel in sorted(rel_files):
                info = zipfile.ZipInfo(rel, date_time=ZIP_EPOCH)
                info.compress_type = method
                info.external_attr = 0
                info.create_system = 3  # platform-independent header bytes
                with archive.open(info, "w") as dst, open(pack / Path(rel), "rb") as srcf:
                    shutil.copyfileobj(srcf, dst, CHUNK)
                # ZipFile.open(..., "w") force-sets external_attr 0o600<<16 when it
                # is 0; reset it before the central directory is written on close.
                info.external_attr = 0
        sha256 = sha256_file(tmp)
        size_bytes = tmp.stat().st_size
        name = f"r-{sha256[:12]}-{sanitize_name(label)}"
        if name in self.by_name:
            fail(f"asset name collision: {name}")
        os.replace(tmp, self.assets_dir / name)
        self.by_name[name] = {
            "name": name,
            "kind": "zipRoot",
            "sha256": sha256,
            "sizeBytes": size_bytes,
            "covers": sorted(rel_files),
        }


def generate(pack: Path, out: Path, revision: str) -> None:
    network_logging_policy.verify(pack)
    portable = load_portable_pack(pack)
    client_jar = portable["clientJar"].replace("\\", "/")
    if client_jar:
        client_path = pack / Path(client_jar)
        if not client_path.is_file():
            fail(f"client jar '{client_jar}' (from portable-pack.json) not found in {pack}")

    per_root: dict[str, list[str]] = {}
    shipped: list[str] = []
    for root in SCAN_ROOTS:
        files = walk_root(pack, root)
        if files:
            per_root[root] = files
            shipped.extend(files)
    for root_file in ("portable-pack.json", client_jar) if client_jar else ("portable-pack.json",):
        if root_file in shipped:
            fail(f"root file '{root_file}' unexpectedly also found under a scanned root")
        shipped.append(root_file)
    shipped.sort()  # ordinal (codepoint) order

    # Hash every shipped file once; single-file assets reuse these hashes.
    file_meta: dict[str, tuple[str, int]] = {}
    for rel in shipped:
        path = pack / Path(rel)
        file_meta[rel] = (sha256_file(path), path.stat().st_size)

    validate_pause_actions(pack, portable, shipped)
    validate_mod_lock(pack, portable, shipped, file_meta)
    legacy_files = load_legacy_files(pack, shipped)

    assets_dir = out / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    hot_patterns = []
    if HOT_MODS_FILE.is_file():
        hot_patterns = [p.lower() for p in
                        json.loads(HOT_MODS_FILE.read_text(encoding="utf-8"))]

    def is_hot(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1].lower()
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in hot_patterns)

    staging = Staging(assets_dir)
    buckets: dict[int, list[str]] = {}
    for rel in per_root.get("mods", []):
        if is_hot(rel):
            sha256, size_bytes = file_meta[rel]
            staging.add_single_file(pack, rel, "m", sha256, size_bytes)
        else:
            digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()
            buckets.setdefault(int(digest[:8], 16) % MODS_CHUNK_COUNT, []).append(rel)
    for bucket, rel_files in sorted(buckets.items()):
        staging.add_mods_chunk(pack, bucket, rel_files)
    for root in ZIP_ROOTS:
        if root in per_root:
            staging.add_zip_root(pack, root, per_root[root])
    for rel in ("portable-pack.json", client_jar) if client_jar else ("portable-pack.json",):
        sha256, size_bytes = file_meta[rel]
        staging.add_single_file(pack, rel, "c", sha256, size_bytes)

    assets = sorted(staging.by_name.values(), key=lambda a: a["name"])

    # Validate: every shipped path covered exactly once, asset names unique (by dict).
    coverage: dict[str, int] = {}
    for asset in assets:
        for rel in asset["covers"]:
            coverage[rel] = coverage.get(rel, 0) + 1
    problems = [rel for rel in shipped if coverage.get(rel, 0) != 1]
    problems += [rel for rel in coverage if rel not in file_meta]
    if problems:
        fail("coverage validation failed for: " + ", ".join(sorted(problems)[:20]))
    for asset in assets:
        staged = assets_dir / asset["name"]
        if not staged.is_file() or staged.stat().st_size != asset["sizeBytes"]:
            fail(f"staged asset missing or size mismatch: {asset['name']}")

    manifest = {
        "schemaVersion": 1,
        # This pack, and not the one this script was copied from: the same
        # short name the publish workflow uses. Three repositories carried
        # this line over unchanged and published manifests calling
        # themselves LL8.
        "packId": "Minecraft 26.3",
        "revision": revision,
        "generatedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minecraftVersion": portable.get("minecraftVersion"),
        # Jars plus what is nested in them. The launcher weighs an
        # installed pack by that number and an unweighed one by this,
        # and the estimate moved by up to 3 GB when only one existed.
        "modCount": count_loaded_mods(pack, shipped),
        "loader": portable.get("loader"),
        "clientJar": client_jar,
        "files": [
            {"path": rel, "sizeBytes": file_meta[rel][1], "sha256": file_meta[rel][0]}
            for rel in shipped
        ],
        "assets": assets,
    }

    if legacy_files:
        manifest["legacyFiles"] = legacy_files

    if (pack / "library/content.json").is_file():
        manifest["library"] = stage_library(pack, out, revision)

    manifest_path = out / "pack-manifest.json"
    tmp_path = out / "pack-manifest.json.tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp_path, manifest_path)

    total = sum(a["sizeBytes"] for a in assets)
    print(f"manifest: {manifest_path}")
    print(f"files: {len(shipped)}, assets: {len(assets)}, "
          f"staged bytes: {total:,}, revision: {revision}")


def verify_only(out: Path) -> None:
    manifest_path = out / "pack-manifest.json"
    if not manifest_path.is_file():
        fail(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("library"):
        verify_library(out, manifest["library"])
    assets = manifest.get("assets") or []
    if not assets:
        fail("manifest contains no assets")
    sample = random.sample(assets, min(VERIFY_SAMPLE, len(assets)))
    for asset in sample:
        staged = out / "assets" / asset["name"]
        if not staged.is_file():
            fail(f"verify: staged asset missing: {asset['name']}")
        actual_sha = sha256_file(staged)
        actual_size = staged.stat().st_size
        if actual_sha != asset["sha256"] or actual_size != asset["sizeBytes"]:
            fail(f"verify: hash/size mismatch for {asset['name']}: "
                 f"expected {asset['sha256']}/{asset['sizeBytes']}, "
                 f"got {actual_sha}/{actual_size}")
        print(f"verify ok: {asset['name']}")
    print(f"verified {len(sample)} of {len(assets)} staged assets")



def count_loaded_mods(pack: pathlib.Path, shipped: list[str]) -> int:
    """Count loader-appropriate unique mod IDs, excluding plain nested libraries."""
    return sum(mod["isLoadedMod"] for mod in read_pack_mods(pack, shipped).values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate (or verify) the LL8 pack release manifest and assets.")
    parser.add_argument("--pack", required=True, help="pack root directory")
    parser.add_argument("--out", required=True, help="dist output directory")
    parser.add_argument("--revision", default=None,
                        help="revision string (default: $GITHUB_SHA or 'local')")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-hash a random sample of staged assets against the manifest")
    args = parser.parse_args()

    pack = Path(args.pack).resolve()
    out = Path(args.out).resolve()
    if not pack.is_dir():
        fail(f"pack root does not exist: {pack}")

    if args.verify_only:
        network_logging_policy.verify(pack)
        verify_only(out)
        return

    revision = args.revision or os.environ.get("GITHUB_SHA") or "local"
    out.mkdir(parents=True, exist_ok=True)
    generate(pack, out, revision)


if __name__ == "__main__":
    main()
