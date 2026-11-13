#!/usr/bin/env python3
"""Build the pack-owned client policy against the descriptor's official native APIs.

The SHA-verified NeoForge installer prepares native patched classes in a private
cache. Only its install-time processors run; no game client or server is started.
Downloaded artifacts are checked against official metadata or Maven sidecars.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile


HERE = Path(__file__).resolve().parent
PACK_ROOT = HERE.parents[1]
MOD_ID = "lanmc_pause_actions"
MOD_VERSION = "1.0.2"
JAR_NAME = f"lanmc-pause-actions-{MOD_VERSION}.jar"
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
ALLOWED_HOSTS = {
    "launchermeta.mojang.com", "piston-meta.mojang.com", "piston-data.mojang.com",
    "libraries.minecraft.net", "maven.neoforged.net",
}
MAX_METADATA = 16 * 1024 * 1024
MAX_ARTIFACT = 256 * 1024 * 1024
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class BuildInputs:
    minecraft_version: str
    neoforge_version: str
    java_version: int
    resource_format: tuple[int, int]
    data_format: tuple[int, int]
    fml_major: int
    classpath: list[Path]


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def checked_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError(f"Unexpected artifact source: {url}")
    return url


def request_bytes(url: str, limit: int = MAX_METADATA) -> bytes:
    request = urllib.request.Request(checked_url(url), headers={"User-Agent": "PauseActions/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        checked_url(response.geturl())
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"Official metadata exceeds the size limit: {url}")
    return payload


def sha256_sidecar(url: str) -> str:
    checksum = request_bytes(url + ".sha256", 1024).decode("ascii").strip().split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError(f"Invalid official Maven SHA256: {url}")
    return checksum


def safe_relative(path: str) -> Path:
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or not parsed.parts or any(p in {".", ".."} or ":" in p for p in parsed.parts):
        raise ValueError(f"Unsafe artifact path: {path}")
    return Path(*parsed.parts)


def download(url: str, target: Path, expected: str, algorithm: str, size: int | None = None) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}" if algorithm == "sha1" else r"[0-9a-f]{64}", expected):
        raise ValueError(f"Invalid {algorithm} for {url}")
    if size is not None and not 0 < size <= MAX_ARTIFACT:
        raise ValueError(f"Invalid official artifact size: {url}")
    if target.is_file() and (size is None or target.stat().st_size == size) and digest(target, algorithm) == expected:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        partial: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="download-", suffix=".part", dir=target.parent, delete=False) as stream:
                partial = Path(stream.name)
                checksum = hashlib.new(algorithm)
                received = 0
                request = urllib.request.Request(checked_url(url), headers={"User-Agent": "PauseActions/1.0"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    checked_url(response.geturl())
                    while block := response.read(1024 * 1024):
                        received += len(block)
                        if received > (size if size is not None else MAX_ARTIFACT):
                            raise ValueError(f"Artifact exceeded official size: {url}")
                        checksum.update(block)
                        stream.write(block)
            if (size is not None and received != size) or checksum.hexdigest() != expected:
                raise ValueError(f"Official artifact checksum/size mismatch: {url}")
            os.replace(partial, target)
            return target
        except (OSError, ValueError):
            if partial is not None:
                partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise AssertionError("Unreachable")


def official_json(payload: bytes) -> dict:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Official metadata must be a JSON object")
    return value


def descriptor_versions(pack_root: Path, minecraft: str | None, neoforge: str | None) -> tuple[str, str]:
    descriptor = official_json((pack_root / "portable-pack.json").read_bytes())
    if descriptor.get("loader", {}).get("type") != "neoforge":
        raise ValueError("Pause Actions requires the pack's NeoForge runtime")
    minecraft = minecraft or descriptor.get("minecraftVersion")
    neoforge = neoforge or descriptor.get("loader", {}).get("version")
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value) for value in (minecraft, neoforge)):
        raise ValueError("Only explicit stable numeric Minecraft and NeoForge versions are supported")
    return minecraft, neoforge


def resolve_inputs(pack_root: Path, cache_dir: Path, minecraft: str | None = None,
                   neoforge: str | None = None) -> BuildInputs:
    minecraft, neoforge = descriptor_versions(pack_root, minecraft, neoforge)
    cache_dir = cache_dir.resolve()
    manifest = official_json(request_bytes(MANIFEST_URL))
    entries = [item for item in manifest.get("versions", []) if item.get("id") == minecraft and item.get("type") == "release"]
    if len(entries) != 1:
        raise ValueError(f"Minecraft {minecraft} is not a unique official stable release")
    entry = entries[0]
    version_path = download(entry["url"], cache_dir / "versions" / minecraft / "version.json", entry["sha1"], "sha1")
    version = official_json(version_path.read_bytes())
    if version.get("id") != minecraft or version.get("type") != "release":
        raise ValueError("Minecraft version identity differs from its official manifest")
    client = version["downloads"]["client"]
    client_path = download(client["url"], cache_dir / "versions" / minecraft / "client.jar", client["sha1"], "sha1", client["size"])
    with zipfile.ZipFile(client_path) as archive:
        embedded = official_json(archive.read("version.json"))
    if embedded.get("id") != minecraft or embedded.get("stable") is not True:
        raise ValueError("Minecraft client is not the requested stable release")
    formats = embedded["pack_version"]
    resource_format = (int(formats["resource_major"]), int(formats["resource_minor"]))
    data_format = (int(formats["data_major"]), int(formats["data_minor"]))
    java_version = int(embedded["java_version"])

    maven_base = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{neoforge}/neoforge-{neoforge}"
    installer_url = maven_base + "-installer.jar"
    installer = download(installer_url, cache_dir / "neoforge" / neoforge / "installer.jar", sha256_sidecar(installer_url), "sha256")
    with zipfile.ZipFile(installer) as archive:
        neo = official_json(archive.read("version.json"))
        install_profile_bytes = archive.read("install_profile.json")
        install_profile = official_json(install_profile_bytes)
    if neo.get("inheritsFrom") != minecraft or neo.get("id") != "neoforge-" + neoforge:
        raise ValueError("Official NeoForge installer does not match the selected Minecraft runtime")
    universal_url = maven_base + "-universal.jar"
    universal = download(universal_url, cache_dir / "neoforge" / neoforge / "universal.jar", sha256_sidecar(universal_url), "sha256")

    libraries: dict[str, dict] = {}
    runtime_libraries: set[str] = set()
    fml_major: int | None = None
    for library in [*version.get("libraries", []), *neo.get("libraries", [])]:
        parts = library["name"].split(":")
        if len(parts) > 3 and parts[3].startswith("natives-"):
            continue
        artifact = library.get("downloads", {}).get("artifact")
        if not artifact:
            continue
        relative = artifact.get("path")
        if not relative:
            raise ValueError(f"Missing official library path: {library['name']}")
        previous = libraries.get(relative)
        if previous is not None and previous != artifact:
            raise ValueError(f"Conflicting official library metadata: {relative}")
        libraries[relative] = artifact
        runtime_libraries.add(relative)
        if parts[:2] == ["net.neoforged.fancymodloader", "loader"]:
            fml_major = int(parts[2].split(".")[0])
    if fml_major is None:
        raise ValueError("Official NeoForge metadata has no Java FML loader")
    for library in install_profile.get("libraries", []):
        artifact = library.get("downloads", {}).get("artifact")
        if artifact is None:
            continue
        relative = artifact.get("path")
        if not relative:
            raise ValueError(f"Missing official processor library path: {library['name']}")
        previous = libraries.get(relative)
        if previous is not None and previous != artifact:
            raise ValueError(f"Conflicting official processor library metadata: {relative}")
        libraries[relative] = artifact

    def fetch(item: tuple[str, dict]) -> Path:
        relative, artifact = item
        return download(artifact["url"], cache_dir / "libraries" / safe_relative(relative),
                        artifact["sha1"], "sha1", int(artifact["size"]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        downloaded = dict(zip(sorted(libraries), pool.map(fetch, sorted(libraries.items()))))
    patched = prepare_native_runtime(cache_dir, minecraft, neoforge, installer,
                                     client_path, version_path, install_profile_bytes,
                                     install_profile, downloaded)
    classpath = [patched, client_path, universal, *[downloaded[key] for key in sorted(runtime_libraries)]]
    return BuildInputs(minecraft, neoforge, java_version, resource_format, data_format, fml_major, classpath)


def maven_jar_path(coordinate: str) -> Path:
    parts = coordinate.split(":")
    if len(parts) not in (3, 4) or any(not re.fullmatch(r"[A-Za-z0-9_.+-]+", part) for part in parts):
        raise ValueError(f"Unsupported official patched artifact coordinate: {coordinate}")
    group, artifact, version = parts[:3]
    filename = artifact + "-" + version + ("-" + parts[3] if len(parts) == 4 else "") + ".jar"
    return safe_relative("/".join([*group.split("."), artifact, version, filename]))


def cache_child(cache_dir: Path, path: Path) -> Path:
    resolved = path.resolve()
    if cache_dir.resolve() not in resolved.parents:
        raise ValueError(f"Native installer path escapes its private cache: {path}")
    return resolved


def prepare_native_runtime(cache_dir: Path, minecraft: str, neoforge: str, installer: Path,
                           client: Path, version_json: Path, profile_bytes: bytes,
                           profile: dict, libraries: dict[str, Path]) -> Path:
    if profile.get("minecraft") != minecraft or profile.get("version") != "neoforge-" + neoforge:
        raise ValueError("NeoForge install profile differs from the selected native runtime")
    declaration = profile.get("data", {}).get("PATCHED", {}).get("client")
    if not isinstance(declaration, str) or not re.fullmatch(r"\[[^\[\]]+\]", declaration):
        raise ValueError("Official installer does not declare its patched client artifact")
    patched_relative = maven_jar_path(declaration[1:-1])
    # Keep the private runtime at the cache root. Nesting a second libraries tree
    # would exceed Windows' path limit for long official Maven coordinates.
    runtime = cache_dir.resolve()
    patched = cache_child(cache_dir, runtime / "libraries" / patched_relative)
    state_path = cache_child(cache_dir, runtime / f"native-inputs-{minecraft}-{neoforge}.json")
    fingerprint = {
        "preparationVersion": 2,
        "minecraft": minecraft, "neoforge": neoforge,
        "clientSha256": digest(client), "versionJsonSha256": digest(version_json),
        "installerSha256": digest(installer),
        "installProfileSha256": hashlib.sha256(profile_bytes).hexdigest(),
        "patchedCoordinate": declaration[1:-1],
        "libraries": {name: digest(path) for name, path in sorted(libraries.items())},
    }
    fingerprint_hash = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if patched.is_file() and state_path.is_file():
        try:
            state = official_json(state_path.read_bytes())
            if (state.get("inputsSha256") == fingerprint_hash
                    and state.get("patchedSizeBytes") == patched.stat().st_size
                    and state.get("patchedSha256") == digest(patched)):
                return patched
        except (ValueError, OSError):
            pass

    runtime.mkdir(parents=True, exist_ok=True)
    version_dir = cache_child(cache_dir, runtime / "versions" / minecraft)
    version_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(version_json, cache_child(cache_dir, version_dir / (minecraft + ".json")))
    shutil.copyfile(client, cache_child(cache_dir, version_dir / (minecraft + ".jar")))
    launcher_profile = cache_child(cache_dir, runtime / "launcher_profiles.json")
    launcher_profile.write_text(json.dumps({"profiles": {}, "settings": {}, "version": 3}), encoding="utf-8")
    for relative, source in sorted(libraries.items()):
        destination = cache_child(cache_dir, runtime / "libraries" / safe_relative(relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination:
            shutil.copyfile(source, destination)
    # An invalid prior result must not make the installer's processor cache skip
    # rebuilding from the verified official inputs. Every affected path is in cache.
    patched.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    log = cache_child(cache_dir, runtime / "native-installer.log")
    temporary = cache_child(cache_dir, runtime / "tmp")
    temporary.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for key in ("TMP", "TEMP", "TMPDIR"):
        environment[key] = str(temporary)
    # Also reaches Java child processors on Unix, where java.io.tmpdir does not
    # derive from TMPDIR. This environment applies only to this private installer.
    environment["JAVA_TOOL_OPTIONS"] = (environment.get("JAVA_TOOL_OPTIONS", "")
                                         + f' -Djava.io.tmpdir="{temporary}"').strip()
    with log.open("w", encoding="utf-8") as output:
        completed = subprocess.run([jdk_tool("java"), "-Djava.awt.headless=true", "-Djava.io.tmpdir=" + str(temporary),
                                    "-jar", str(installer.resolve()),
                                    "--installClient", str(runtime)], cwd=runtime, stdout=output,
                                   stderr=subprocess.STDOUT, timeout=600, env=environment)
    if completed.returncode != 0 or not patched.is_file():
        detail = log.read_text(encoding="utf-8", errors="replace")[-6000:]
        raise RuntimeError(f"Official NeoForge native preparation failed ({completed.returncode}):\n{detail}")
    with zipfile.ZipFile(patched) as archive:
        if "net/minecraft/client/gui/components/Button.class" not in archive.namelist():
            raise ValueError("Official patched client has no native Button class")
    state = {"schemaVersion": 1, "inputsSha256": fingerprint_hash, "inputs": fingerprint,
             "patchedPath": patched_relative.as_posix(), "patchedSizeBytes": patched.stat().st_size,
             "patchedSha256": digest(patched), "gameStarted": False}
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return patched


def jdk_tool(name: str) -> str:
    executable = name + (".exe" if os.name == "nt" else "")
    java_home = os.environ.get("JAVA_HOME")
    candidate = Path(java_home) / "bin" / executable if java_home else None
    if candidate is not None and candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise ValueError(f"JDK tool {name} was not found; set JAVA_HOME to the pack's JDK")
    return found


def compile_java(sources: list[Path], classpath: list[Path], output: Path, java_version: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    arguments = ["-encoding", "UTF-8", "--release", str(java_version), "-g:none", "-classpath",
                 os.pathsep.join(str(path.resolve()) for path in classpath), "-d", str(output.resolve()),
                 *[str(path.resolve()) for path in sources]]
    # javac's argument file avoids Windows' command-line length limit, and is portable.
    argument_file = output.parent / "javac.args"
    argument_file.write_text("\n".join('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"' for value in arguments), encoding="utf-8")
    subprocess.run([jdk_tool("javac"), "@" + str(argument_file.resolve())], check=True)


def build(pack_root: Path, cache_dir: Path, output: Path, minecraft: str | None = None,
          neoforge: str | None = None) -> dict:
    output = output.resolve()
    if output == (pack_root / "mods").resolve() or (pack_root / "mods").resolve() in output.parents:
        raise ValueError("Build output must remain outside the active mods directory")
    inputs = resolve_inputs(pack_root, cache_dir, minecraft, neoforge)
    source_root = HERE / "src" / "main"
    sources = sorted((source_root / "java").rglob("*.java"))
    if not sources:
        raise ValueError("No Pause Actions Java sources were found")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compile-", dir=cache_dir) as temp:
        classes = Path(temp) / "classes"
        compile_java(sources, inputs.classpath, classes, inputs.java_version)
        replacements = {
            "@MINECRAFT@": inputs.minecraft_version, "@NEOFORGE@": inputs.neoforge_version,
            "@FML_MAJOR@": str(inputs.fml_major), "@JAVA_VERSION@": str(inputs.java_version),
            "@MIN_FORMAT@": json.dumps(min(inputs.resource_format, inputs.data_format)),
            "@MAX_FORMAT@": json.dumps(max(inputs.resource_format, inputs.data_format)),
        }
        entries = {path.relative_to(classes).as_posix(): path.read_bytes() for path in classes.rglob("*.class")}
        entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\r\nFMLModType: MOD\r\nAutomatic-Module-Name: lanmc.pause_actions\r\n\r\n"
        entries["META-INF/lanmc-pause-actions.json"] = (HERE / "contract.json").read_bytes()
        for resource in sorted((source_root / "resources").rglob("*")):
            if resource.is_file():
                text = resource.read_text(encoding="utf-8")
                for key, value in replacements.items():
                    text = text.replace(key, value)
                if re.search(r"@[A-Z_]+@", text):
                    raise ValueError(f"Unresolved template in {resource}")
                name = resource.relative_to(source_root / "resources").as_posix().removesuffix(".in")
                entries[name] = text.replace("\r\n", "\n").encode("utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        # This tiny component uses stored entries: zlib versions cannot change the
        # committed artifact when the same sources compile on Windows and Linux.
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, payload in sorted(entries.items()):
                info = zipfile.ZipInfo(name, ZIP_EPOCH)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, payload)
    return {"path": str(output), "sizeBytes": output.stat().st_size, "sha256": digest(output),
            "minecraft": inputs.minecraft_version, "neoforge": inputs.neoforge_version,
            "java": inputs.java_version, "resourceFormat": inputs.resource_format,
            "dataFormat": inputs.data_format}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=PACK_ROOT)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minecraft")
    parser.add_argument("--neoforge")
    args = parser.parse_args()
    pack_root = args.pack.resolve()
    cache_dir = (args.cache_dir or pack_root / ".cache" / "pause-actions").resolve()
    print(json.dumps(build(pack_root, cache_dir, args.output, args.minecraft, args.neoforge), indent=2))


if __name__ == "__main__":
    main()
