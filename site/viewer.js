"use strict";

const byId = id => document.getElementById(id);
const leftRun = byId("left-run");
const rightRun = byId("right-run");
const leftMode = byId("left-mode");
const rightMode = byId("right-mode");
const scenePicker = byId("scene");
const preview = byId("comparison");
const status = byId("status");
const canvas = byId("difference");
const context = canvas.getContext("2d");
const gain = byId("gain");
const modes = { eco: "Eco", balanced: "Balanced", high: "High" };
const cache = new Map();
const hardwareKeys = ["gpu", "backend", "driver", "driver_info", "cpu", "os", "arch"];
let newestSelection = 0;
let pixels;

function showStatus(message) {
  status.hidden = !message;
  status.textContent = message;
}

async function getJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`Failed to load ${path} (${response.status})`);
  return response.json();
}

function loadRun(id) {
  if (!cache.has(id)) {
    const pending = getJson(`./runs/${encodeURIComponent(id)}/run.json`);
    pending.catch(() => cache.delete(id));
    cache.set(id, pending);
  }
  return cache.get(id);
}

function runLabel(run) {
  const timestamp = run.created.slice(0, 16).replace("T", " ");
  return `${run.ref} · ${run.commit.slice(0, 8)} · ${timestamp} UTC`;
}

function median(samples) {
  const sorted = [...samples].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function meter(side, mode, record) {
  const box = document.createElement("div");
  box.className = "meter";
  const title = document.createElement("div");
  title.textContent = `${side} · ${modes[mode]}`;
  const time = document.createElement("strong");
  time.textContent = `${median(record.render_ms).toFixed(2)} ms`;
  const settings = document.createElement("small");
  settings.textContent = `${record.trace_width} × ${record.trace_height} traced · ${record.preset.max_bounces} bounces · ${record.preset.vegetation_percent}% vegetation`;
  box.append(title, time, settings);
  return box;
}

function imageUrl(run, capture) {
  return `./runs/${encodeURIComponent(run.id)}/${encodeURIComponent(capture.image)}`;
}

function loadImage(url) {
  const image = new Image();
  image.src = url;
  return image.decode().then(() => image);
}

function updateGain() {
  byId("gain-value").value = `${gain.value}×`;
  if (!pixels) return;
  const output = context.createImageData(canvas.width, canvas.height);
  const multiplier = Number(gain.value);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    for (let channel = 0; channel < 3; channel++) {
      output.data[offset + channel] = pixels[offset + channel] * multiplier;
    }
    output.data[offset + 3] = 255;
  }
  context.putImageData(output, 0, 0);
}

function difference(left, right) {
  canvas.width = left.naturalWidth;
  canvas.height = left.naturalHeight;
  const work = document.createElement("canvas");
  work.width = canvas.width;
  work.height = canvas.height;
  const scratch = work.getContext("2d", { willReadFrequently: true });
  const size = canvas.width * canvas.height;
  const rgba = image => {
    scratch.clearRect(0, 0, work.width, work.height);
    scratch.drawImage(image, 0, 0);
    return scratch.getImageData(0, 0, work.width, work.height).data;
  };
  const a = rgba(left);
  const b = rgba(right);
  pixels = new Uint8Array(a.length);
  let sum = 0;
  let changed = 0;
  for (let offset = 0; offset < a.length; offset += 4) {
    let max = 0;
    for (let channel = 0; channel < 3; channel++) {
      const delta = Math.abs(a[offset + channel] - b[offset + channel]);
      pixels[offset + channel] = delta;
      sum += delta;
      max = Math.max(max, delta);
    }
    if (max > 1) changed++;
  }
  updateGain();
  byId("diff-stats").textContent =
    `Mean absolute RGB difference ${(sum / (size * 3)).toFixed(3)} / 255 · ${(100 * changed / size).toFixed(2)}% of pixels changed by more than 1 / 255`;
}

function deviceLabel(hardware) {
  return [hardware.gpu, hardware.backend, hardware.driver_info].filter(Boolean).join(" · ");
}

async function refresh() {
  const selection = ++newestSelection;
  const initialScene = scenePicker.value;
  preview.hidden = true;
  showStatus("Loading comparison…");
  try {
    const [left, right] = await Promise.all([loadRun(leftRun.value), loadRun(rightRun.value)]);
    if (selection !== newestSelection) return;
    const common = left.scenes.filter(scene =>
      right.scenes.some(candidate => candidate.id === scene.id && candidate.capture_key === scene.capture_key));
    scenePicker.replaceChildren(...common.map(scene => new Option(scene.label, scene.id)));
    if (!common.length) {
      showStatus("These runs have no scene with matching inputs.");
      return;
    }
    if (common.some(scene => scene.id === initialScene)) scenePicker.value = initialScene;
    const scene = common.find(item => item.id === scenePicker.value);
    const rightScene = right.scenes.find(item => item.id === scene.id);
    const leftCapture = scene.modes[leftMode.value];
    const rightCapture = rightScene.modes[rightMode.value];
    const urls = [imageUrl(left, leftCapture), imageUrl(right, rightCapture)];
    const images = await Promise.all(urls.map(loadImage));
    if (selection !== newestSelection) return;
    const inputs = scene.inputs;
    if (images.some(image => image.naturalWidth !== inputs.width || image.naturalHeight !== inputs.height)) {
      throw new Error("A render has unexpected dimensions.");
    }
    byId("scene-name").textContent = scene.label;
    byId("inputs").textContent = `${inputs.width} × ${inputs.height} output · ${inputs.samples} samples per pixel · exposure ${inputs.exposure}`;
    const sameHardware = hardwareKeys.every(key => left.hardware[key] === right.hardware[key]);
    byId("devices").textContent = sameHardware ? deviceLabel(left.hardware) :
      `Left ${deviceLabel(left.hardware)} / Right ${deviceLabel(right.hardware)}`;
    byId("timings").replaceChildren(meter("Left", leftMode.value, leftCapture), meter("Right", rightMode.value, rightCapture));
    const ratio = median(leftCapture.render_ms) / median(rightCapture.render_ms);
    byId("timing-note").textContent = sameHardware ?
      `Left ÷ right ${ratio.toFixed(2)}× · median of ${left.repeats} / ${right.repeats} renders · encoding and GPU waits, before PNG export` :
      `Different hardware or drivers · median of ${left.repeats} / ${right.repeats} renders`;
    for (const [index, side] of ["left", "right"].entries()) {
      byId(`${side}-image`).src = urls[index];
      byId(`${side}-image-link`).href = urls[index];
      const mode = index === 0 ? leftMode.value : rightMode.value;
      byId(`${side}-caption`).textContent = modes[mode];
    }
    difference(...images);
    showStatus("");
    preview.hidden = false;
  } catch (error) {
    if (selection === newestSelection) showStatus(error.message);
  }
}

async function start() {
  try {
    const runs = await getJson("./runs/index.json");
    if (!runs.length) {
      showStatus("No benchmark runs have been published.");
      return;
    }
    for (const picker of [leftRun, rightRun]) {
      picker.replaceChildren(...runs.map(run => new Option(runLabel(run), run.id)));
      picker.addEventListener("change", refresh);
    }
    for (const picker of [leftMode, rightMode]) {
      picker.replaceChildren(...Object.entries(modes).map(([value, label]) => new Option(label, value)));
      picker.addEventListener("change", refresh);
    }
    leftRun.selectedIndex = runs.length > 1 ? 1 : 0;
    leftMode.value = runs.length > 1 ? "balanced" : "eco";
    rightMode.value = runs.length > 1 ? "balanced" : "high";
    scenePicker.addEventListener("change", refresh);
    gain.addEventListener("input", updateGain);
    byId("benchmark").hidden = false;
    await refresh();
  } catch (error) {
    showStatus(error.message);
  }
}

start();
