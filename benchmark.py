#!/usr/bin/env python3
"""Capture Stargaze revisions on this machine and update the GitHub Pages data."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import struct
import subprocess
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "site" / "runs"
MODES = ("eco", "balanced", "high")
DEVICE_FIELDS = ("gpu", "backend", "driver", "driver_info")


def run_command(args, *, cwd=None, env=None, capture=False):
    return subprocess.run(args, cwd=cwd, env=env, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def git(source, *args):
    return run_command(["git", "-C", source, *args], capture=True).strip()


def resolve(source, ref):
    for candidate in (ref, f"refs/remotes/origin/{ref}"):
        result = subprocess.run(
            ["git", "-C", source, "rev-parse", "--verify", "--end-of-options", f"{candidate}^{{commit}}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    raise ValueError(f"Cannot resolve {ref!r} in {source}. Fetch or check the ref name.")


def dump_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def update_index():
    RUNS.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(RUNS.glob("*/run.json"), reverse=True):
        if path.parent.name.startswith("."):
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        items.append({key: run[key] for key in ("id", "ref", "commit", "created", "hardware")})
    with tempfile.NamedTemporaryFile(dir=RUNS, prefix=".index-", delete=False) as file:
        temporary = Path(file.name)
    try:
        dump_json(temporary, items)
        temporary.replace(RUNS / "index.json")
    finally:
        temporary.unlink(missing_ok=True)


def cpu_name():
    info = Path("/proc/cpuinfo")
    if info.is_file():
        for line in info.read_text().splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    if platform.system() == "Darwin":
        return run_command(["sysctl", "-n", "machdep.cpu.brand_string"], capture=True).strip()
    return platform.processor() or platform.machine()


def verify_capture(data, request, output):
    fields = ("version", "mode", "width", "height", "samples", "exposure")
    if any(data.get(field) != request[field] for field in fields):
        raise ValueError("Renderer metadata disagrees with the render request")
    elapsed = data.get("render_ms")
    if not isinstance(elapsed, (float, int)) or not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("Renderer returned an invalid render time")
    for field in DEVICE_FIELDS:
        if not isinstance(data.get(field), str):
            raise ValueError(f"Renderer returned invalid {field} metadata")
    if not data["gpu"] or not data["backend"]:
        raise ValueError("Renderer did not identify the GPU and backend")
    with output.open("rb") as file:
        header = file.read(24)
    if len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR":
        raise ValueError(f"Renderer output is not PNG: {output.name}")
    if struct.unpack(">II", header[16:]) != (request["width"], request["height"]):
        raise ValueError(f"Wrong PNG dimensions: {output.name}")


def capture_scene(binary, stage, scene, suite, adapter, environment):
    scene_id = scene["id"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scene_id):
        raise ValueError(f"Invalid scene ID: {scene_id!r}")
    scene_path = ROOT / "scenes" / f"{scene_id}.yaml"
    request = {
        "version": 1, "scene": str(scene_path),
        "width": suite["width"], "height": suite["height"],
        "samples": suite["samples"], "exposure": scene["exposure"], "adapter": adapter,
    }
    inputs = {key: request[key] for key in ("version", "width", "height", "samples", "exposure")}
    inputs["scene_sha256"] = hashlib.sha256(scene_path.read_bytes()).hexdigest()
    capture_key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    records = {}
    hardware = None
    request_file = stage / "request.json"
    scratch = stage / "repeat.png"
    try:
        for mode in MODES:
            image = stage / f"{scene_id}-{mode}.png"
            request["mode"] = mode
            dump_json(request_file, request)
            render_times = []
            process_times = []
            for trial in range(suite["repeats"]):
                output = image if trial == 0 else scratch
                start = time.perf_counter()
                stdout = run_command(
                    [binary, "render", "--request", request_file, "--output", output],
                    env=environment, capture=True,
                )
                process_times.append((time.perf_counter() - start) * 1000)
                data = json.loads(stdout)
                verify_capture(data, request, output)
                device = {key: data[key] for key in DEVICE_FIELDS}
                if hardware is not None and device != hardware:
                    raise ValueError("GPU or driver changed within the run")
                hardware = device
                render_times.append(data["render_ms"])
            record = {
                "image": image.name,
                "trace_width": data["trace_width"],
                "trace_height": data["trace_height"],
                "preset": data["preset"],
                "render_ms": render_times,
                "process_ms": process_times,
            }
            records[mode] = record
            print(f"  {scene_id:16} {mode:8} {statistics.median(render_times):8.2f} ms", flush=True)
    finally:
        request_file.unlink(missing_ok=True)
        scratch.unlink(missing_ok=True)
    result = {"id": scene_id, "label": scene["label"], "capture_key": capture_key,
              "inputs": inputs, "modes": records}
    return result, hardware


def capture_run(binary, ref, commit, suite, adapter):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("STARGAZE_")}
    now = datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%dT%H%M%S%fZ}-{commit[:12]}"
    RUNS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=RUNS) as temporary:
        stage = Path(temporary)
        cases = []
        hardware = None
        for scene in suite["scenes"]:
            result, device = capture_scene(binary, stage, scene, suite, adapter, environment)
            if hardware is not None and device != hardware:
                raise ValueError("GPU or driver changed within the run")
            hardware = device
            cases.append(result)
        hardware.update(cpu=cpu_name(), os=platform.system(), arch=platform.machine())
        dump_json(stage / "run.json", {
            "version": 1, "id": run_id, "ref": ref, "commit": commit, "created": now.isoformat(),
            "hardware": hardware, "repeats": suite["repeats"], "scenes": cases,
        })
        stage.rename(RUNS / run_id)
    update_index()
    print(f"Saved {RUNS / run_id}", flush=True)


def build(source, checkout, temporary, args):
    binary = temporary / ("stargaze.exe" if os.name == "nt" else "stargaze")
    if args.builder:
        run_command([args.builder.resolve(), checkout, binary], cwd=checkout)
    else:
        target = args.target_dir or (ROOT / ".cache" / "cargo-target")
        target = target.resolve()
        run_command(["cargo", "build", "--locked", "--release", "--bin", "stargaze",
                     "--target-dir", target], cwd=checkout)
        shutil.copy2(target / "release" / binary.name, binary)
    if not binary.is_file():
        raise ValueError(f"Builder did not produce {binary.name} for {source}")
    return binary


def run(args):
    if args.publish and git(ROOT, "branch", "--show-current") != "main":
        raise ValueError("--publish runs from the benchmark repository's main branch")
    source = args.source.resolve()
    git(source, "rev-parse", "--show-toplevel")
    refs = [(ref, resolve(source, ref)) for ref in args.refs]
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    if suite.get("version") != 1 or not suite.get("scenes"):
        raise ValueError("Expected a version 1 benchmark suite with at least one scene")
    if len({scene["id"] for scene in suite["scenes"]}) != len(suite["scenes"]):
        raise ValueError("Suite contains duplicate scene IDs")
    if not isinstance(suite["repeats"], int) or suite["repeats"] < 1:
        raise ValueError("Suite repeats must be a positive integer")
    for ref, commit in refs:
        print(f"{ref} ({commit})", flush=True)
        with tempfile.TemporaryDirectory(prefix="stargaze-benchmark-") as temporary:
            directory = Path(temporary)
            checkout = directory / "source"
            git(source, "worktree", "add", "--detach", checkout, commit)
            try:
                binary = build(ref, checkout, directory, args)
                capture_run(binary, ref, commit, suite, args.adapter)
            finally:
                git(source, "worktree", "remove", "--force", checkout)
    if args.publish:
        run_command(["git", "add", "--", "site/runs"], cwd=ROOT)
        run_command(["git", "commit", "--only", "-m", "Record Stargaze benchmark runs",
                     "--", "site/runs"], cwd=ROOT)
        run_command(["git", "push", "origin", "HEAD:main"], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("run", help="build and capture Git branches, tags, or hashes")
    execute.add_argument("refs", nargs="+")
    execute.add_argument("--source", type=Path, default=ROOT.parent / "stargaze")
    execute.add_argument("--suite", type=Path, default=ROOT / "suite.json")
    execute.add_argument("--adapter", default="", help="GPU name substring")
    execute.add_argument("--target-dir", type=Path, help="shared Cargo build cache")
    execute.add_argument("--builder", type=Path, help="executable called with the checkout and output binary paths")
    execute.add_argument("--publish", action="store_true", help="commit the captures and push main to GitHub Pages")
    sub.add_parser("index", help="rebuild the static index from saved runs")
    args = parser.parse_args()
    try:
        if args.command == "run":
            run(args)
        else:
            update_index()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Benchmark failed: {error}\n")


if __name__ == "__main__":
    main()
