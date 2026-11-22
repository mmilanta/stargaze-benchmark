# Stargaze Renderer Benchmark

[Open the benchmark dashboard](https://mmilanta.github.io/stargaze-benchmark/).
Choose a Stargaze run and graphics preset on each side, then choose a scene and
a shared sample count. The dashboard shows median render times, both captures,
an amplified difference image, and descriptive RGB difference statistics. There
is no ground-truth image or quality score.

## Capture a run

Use Python 3.10+, Git, a hardware GPU, and the tools needed to build Stargaze.
Keep a Stargaze checkout beside this repo, or pass `--source`. Benchmark refs
should come from sources you trust because their builds and binaries run locally.

```sh
git -C ../stargaze fetch origin
python3 benchmark.py run origin/benchmark/headless-baseline origin/benchmark/headless-render-v1
```

Each argument is a branch, tag, or commit hash in the source checkout. The runner
resolves every argument to a commit before building, uses disposable detached
worktrees, and reads the pinned scenes under `scenes/`. A run captures Eco,
Balanced, and High at **1, 16, and 64 samples per pixel** for each scene. The app
selects its own preset settings and reports the effective trace resolution and
settings with each capture.
`--adapter 'RX 7600'` selects a specific GPU by name. `--suite FILE` accepts
another version 1 suite using the checked-in scenes, and `--target-dir PATH`
can reuse a Cargo build cache. The suite's `samples` setting accepts a single
count or a list of counts.

Only revisions supporting the [headless render interface](#render-interface)
can be run directly. The two initial refs carry the same interface on top of
Stargaze commits `852ab1c` and `6127a80`. The default builder uses
`cargo build --locked --release --bin stargaze`. A different implementation can
supply `--builder /path/to/executable`. The builder receives the source checkout
and the destination path for the CLI executable.

## Publish runs

Every successful run updates `site/runs/`. Pass `--publish` to the `run` command
from `main` to commit the captures and push them after the requested runs finish.
Preview with `python3 -m http.server --bind 127.0.0.1 --directory site`. You can
also publish previously captured data manually.

```sh
git add site/runs
git commit -m "Record Stargaze benchmark runs"
git push origin main
```

The Pages workflow ships the static files. The GPU workload runs only when you
invoke the local benchmark. Run `python3 benchmark.py index` if you need to
regenerate the index from saved run folders.

## Render interface

```sh
stargaze render --request request.json --output frame.png
```

```json
{
  "version": 1,
  "scene": "scenes/crescent-moon.yaml",
  "mode": "balanced",
  "width": 1280,
  "height": 720,
  "samples": 16,
  "exposure": 64,
  "adapter": "RX 7600"
}
```

The scene path is absolute or relative to the request file. A scene has an
explicit starting time. The supported modes are `eco`, `balanced`, and `high`.
The command writes an sRGB PNG at the requested output dimensions. On success,
stdout is a single JSON object with the request version, mode, dimensions,
sample count and exposure, plus `trace_width`, `trace_height`, effective
`preset` settings, `gpu`, `backend`, `driver`, `driver_info`, and `render_ms`.
The optional adapter filter is a case-insensitive GPU name substring.

The runner makes a fresh process for each repeat and keeps the first image. The
dashboard uses the median of the per-process `render_ms` values. This time
covers tracing, render submissions and GPU waits, up to PNG readback. It omits
application startup, scene assembly and PNG export. The saved run JSON also
includes end-to-end process timings. Display pacing and the preset’s
interactive FPS cap have no effect on these offscreen captures.

The viewer compares matching scene files, output dimensions, sample count, and
exposure. Presets may intentionally differ. If GPU, driver, or CPU metadata
differs, the viewer shows both timings individually. The difference panel
reports the mean absolute per-channel RGB difference and the fraction of pixels
whose largest RGB difference exceeds one 8-bit step. Its gain affects the
difference image only.
