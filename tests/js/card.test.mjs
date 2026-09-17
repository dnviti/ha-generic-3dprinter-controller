/**
 * jsdom tests for the Generic 3D Printer Controller card.
 *
 * The card's one hard rule is that it draws a control only when the backend reports
 * the capability for it. Most of these tests exist to hold that rule down, because
 * the failure mode is a button that looks available and fails when pressed.
 *
 * Run with: npm test
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test, { after } from "node:test";

import { JSDOM } from "jsdom";

/* The card starts a refresh interval on connect, which would keep the test runner
 * alive forever. Every mounted card is torn down at the end of the run, and the
 * process is then ended explicitly so a stray timer cannot hang CI. */
const mounted = [];

after(() => {
  for (const { card, dom } of mounted) {
    try {
      card.remove();
      dom.window.close();
    } catch {
      // A closed window is not a failure.
    }
  }
  setTimeout(() => process.exit(0), 50).unref();
});

const HERE = dirname(fileURLToPath(import.meta.url));
const CARD_PATH = resolve(
  HERE,
  "../../custom_components/generic_3dprinter/www/generic-3dprinter-card.js",
);

/** A snapshot shaped exactly like the one the Python adapter produces. */
function snapshot(overrides = {}) {
  return {
    protocol: "sdcp_cc1",
    connected: true,
    print_state: "printing",
    capabilities: [
      "pause",
      "resume",
      "stop",
      "set_hotend_temp",
      "set_bed_temp",
      "set_chamber_temp",
      "set_fan_speed",
      "set_speed",
      "set_light",
      "file_list",
      "camera",
      "web_ui",
    ],
    progress: 42.0,
    current_layer: 264,
    total_layers: 627,
    remaining: 5100.5,
    elapsed: 2400.0,
    filename: "/local/ECC_0.4_part_PLA_0.2_5h10m.gcode",
    job_id: "3d315103",
    speed_factor: 100.0,
    flow_factor: null,
    hotend: { current: 219.8, target: 220 },
    bed: { current: 55.1, target: 55 },
    chamber: { current: 32.7, target: 0 },
    fans: { model: 100, auxiliary: 69, chamber: 68, hotend: null, controller: null },
    position: { x: 101.1, y: 77.83, z: 22.45 },
    homed_axes: [],
    lights: ["chamber"],
    camera: true,
    model: "Centauri Carbon",
    firmware: "V1.4.49",
    serial: "5c441dd30105041800009c0000000000",
    errors: [],
    ...overrides,
  };
}

function description(overrides = {}) {
  return {
    entry_id: "entry1",
    name: "Centauri Carbon",
    protocol: "sdcp_cc1",
    model: "Centauri Carbon",
    firmware: "V1.4.49",
    connected: true,
    camera: true,
    web_ui: true,
    camera_url: "/api/generic_3dprinter/entry1/camera.mjpeg/tok",
    snapshot_url: "/api/generic_3dprinter/entry1/snapshot.jpg/tok",
    status_url: "/api/generic_3dprinter/entry1/status/tok",
    web_proxy_url: "/api/generic_3dprinter/entry1/web/tok/p80/",
    web_ui_url: "http://192.168.128.143/",
    camera_stats: { live: true, viewers: 1, frames: 120, last_frame_bytes: 45000, last_error: null },
    unsafe_features: [],
    printer: snapshot(),
    ...overrides,
  };
}

/** Build a card in a fresh document with a scripted `hass`. */
async function mountCard({ printers, descriptions, config = {}, failList = false } = {}) {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  global.window = window;
  global.document = window.document;
  global.customElements = window.customElements;
  global.HTMLElement = window.HTMLElement;
  global.CustomEvent = window.CustomEvent;
  global.setInterval = window.setInterval.bind(window);
  global.clearInterval = window.clearInterval.bind(window);

  const source = readFileSync(CARD_PATH, "utf8");
  window.eval(source);

  const calls = [];
  const hass = {
    callWS: async (message) => {
      calls.push(message);
      if (message.type === "generic_3dprinter/list") {
        if (failList) throw new Error("the integration is not loaded");
        return { printers: printers ?? [] };
      }
      if (message.type === "generic_3dprinter/describe") {
        const value = (descriptions ?? {})[message.entry_id];
        if (value === undefined) throw new Error("no such printer");
        return value;
      }
      if (message.type === "generic_3dprinter/send") {
        return { ok: true };
      }
      throw new Error(`unexpected message ${message.type}`);
    },
  };

  const card = window.document.createElement("generic-3dprinter-card");
  card.setConfig(config);
  window.document.body.appendChild(card);
  card.hass = hass;
  await settle(card);
  mounted.push({ card, dom });
  return { card, calls, window, dom };
}

/** Wait for the card's initial round trip and its render to finish. */
async function settle(card) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolveTick) => setTimeout(resolveTick, 5));
    const root = card.shadowRoot;
    if (!root) continue;
    if (root.querySelector(".printer") || root.querySelector(".empty")) return;
  }
  await new Promise((resolveTick) => setTimeout(resolveTick, 40));
}

function texts(card, selector) {
  return [...card.shadowRoot.querySelectorAll(selector)].map((node) => node.textContent.trim());
}

test("renders the printer name, state and secondary line", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Centauri Carbon", protocol: "sdcp_cc1" }],
    descriptions: { entry1: description() },
  });
  assert.deepEqual(texts(card, ".name"), ["Centauri Carbon"]);
  assert.deepEqual(texts(card, ".state"), ["Printing"]);
  assert.match(card.shadowRoot.querySelector(".subtitle").textContent, /Centauri Carbon/);
  assert.match(card.shadowRoot.querySelector(".subtitle").textContent, /V1\.4\.49/);
});

test("shows progress, layers, remaining time and temperatures", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Centauri Carbon" }],
    descriptions: { entry1: description() },
  });
  assert.equal(card.shadowRoot.querySelector(".progress-fill").style.width, "42%");
  assert.deepEqual(texts(card, ".progress-label"), ["42%"]);
  assert.deepEqual(texts(card, ".stat-value"), ["264 / 627", "1h 25m", "40m 00s", "100%"]);
  assert.deepEqual(texts(card, ".temp-value"), ["220 °C → 220 °C", "55 °C → 55 °C", "33 °C"]);
  assert.deepEqual(texts(card, ".job-name"), ["ECC_0.4_part_PLA_0.2_5h10m.gcode"]);
});

test("draws only the controls the printer reports capabilities for", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Printer" }],
    descriptions: {
      entry1: description({
        printer: snapshot({
          print_state: "idle",
          capabilities: ["home", "file_list", "web_ui"],
        }),
      }),
    },
  });
  const labels = texts(card, ".ctl");
  assert.deepEqual(labels, ["Home"]);
  assert.ok(!labels.includes("Pause"));
  assert.ok(!labels.includes("Stop"));
});

test("a printer that cannot pause gets no pause control and no progress bar", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Web only" }],
    descriptions: {
      entry1: description({
        printer: snapshot({
          protocol: "web_only",
          print_state: "unknown",
          capabilities: ["web_ui"],
          progress: null,
          current_layer: null,
          total_layers: null,
          remaining: null,
          elapsed: null,
          filename: null,
          hotend: { current: null, target: null },
          bed: { current: null, target: null },
          chamber: { current: null, target: null },
        }),
        model: null,
        firmware: null,
        camera: false,
        snapshot_url: null,
      }),
    },
  });
  assert.equal(card.shadowRoot.querySelector(".ctl"), null);
  assert.equal(card.shadowRoot.querySelector(".progress-fill"), null);
  assert.equal(card.shadowRoot.querySelector(".camera img"), null);
  // A printer with no API must not be described as idle, because nobody knows.
  assert.deepEqual(texts(card, ".state"), ["Unknown"]);
});

test("a printer without a camera renders no camera pane", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "No camera" }],
    descriptions: {
      entry1: description({
        printer: snapshot({ capabilities: ["pause", "stop", "set_hotend_temp", "set_bed_temp"] }),
        camera: false,
        snapshot_url: null,
      }),
    },
  });
  assert.equal(card.shadowRoot.querySelector(".camera"), null);
  assert.ok(texts(card, ".ctl").includes("Pause"));
});

test("the camera pane loads the live stream, not a still snapshot", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Camera" }],
    descriptions: { entry1: description() },
  });
  const image = card.shadowRoot.querySelector(".camera img");
  assert.ok(image, "expected a camera image");
  // A snapshot only changes when the card polls, which is what made a live camera
  // look like a slideshow.
  assert.equal(image.getAttribute("src"), "/api/generic_3dprinter/entry1/camera.mjpeg/tok");
  assert.equal(image.dataset.mode, "stream");
});

test("without a stream url the camera falls back to the still", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Camera" }],
    descriptions: { entry1: description({ camera_url: null }) },
  });
  const image = card.shadowRoot.querySelector(".camera img");
  assert.ok(image, "expected a camera image");
  assert.equal(image.getAttribute("src"), "/api/generic_3dprinter/entry1/snapshot.jpg/tok");
  assert.equal(image.dataset.mode, "snapshot");
});

test("a stream that fails degrades to the still instead of vanishing", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Camera" }],
    descriptions: { entry1: description() },
  });
  const image = card.shadowRoot.querySelector(".camera img");
  image.dispatchEvent(new card.ownerDocument.defaultView.Event("error"));
  assert.equal(image.dataset.mode, "snapshot");
  assert.equal(image.getAttribute("src"), "/api/generic_3dprinter/entry1/snapshot.jpg/tok");
});

test("redrawing the card keeps the same stream url so the stream is not restarted", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Camera" }],
    descriptions: { entry1: description() },
  });
  const before = card.shadowRoot.querySelector(".camera img").getAttribute("src");
  await card._refreshAll();
  const after = card.shadowRoot.querySelector(".camera img").getAttribute("src");
  // An identical src is a no-op in the DOM, so an existing viewer keeps its one
  // upstream connection. A changing url would tear the stream down every poll.
  assert.equal(after, before);
});

test("pressing pause sends the normalised command over the websocket api", async () => {
  const { card, calls } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Printer" }],
    descriptions: { entry1: description() },
  });
  const pause = [...card.shadowRoot.querySelectorAll(".ctl")].find(
    (node) => node.textContent.trim() === "Pause",
  );
  assert.ok(pause, "expected a pause button while printing");
  assert.equal(pause.disabled, false);

  pause.click();
  await new Promise((resolveTick) => setTimeout(resolveTick, 20));

  const sent = calls.filter((call) => call.type === "generic_3dprinter/send");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].entry_id, "entry1");
  assert.equal(sent[0].command, "pause");
  assert.deepEqual(Object.keys(sent[0].data), []);
});

test("pause is disabled while the printer is not printing", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Printer" }],
    descriptions: {
      entry1: description({ printer: snapshot({ print_state: "paused" }) }),
    },
  });
  const labels = texts(card, ".ctl");
  assert.ok(labels.includes("Pause"));
  const pause = [...card.shadowRoot.querySelectorAll(".ctl")].find(
    (node) => node.textContent.trim() === "Pause",
  );
  assert.equal(pause.disabled, true);
  const resume = [...card.shadowRoot.querySelectorAll(".ctl")].find(
    (node) => node.textContent.trim() === "Resume",
  );
  assert.equal(resume.disabled, false);
});

test("an offline printer says so instead of showing stale numbers", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Printer" }],
    descriptions: {
      entry1: description({
        connected: false,
        last_error: "the printer is unreachable",
        printer: snapshot({ connected: false, errors: ["the printer is unreachable"] }),
      }),
    },
  });
  const warning = card.shadowRoot.querySelector(".warning");
  assert.ok(warning, "expected an offline warning");
  assert.match(warning.textContent, /unreachable/);
});

test("the fleet mode renders every configured printer", async () => {
  const { card } = await mountCard({
    printers: [
      { entry_id: "entry1", name: "First" },
      { entry_id: "entry2", name: "Second" },
    ],
    descriptions: {
      entry1: description({ entry_id: "entry1", name: "First" }),
      entry2: description({
        entry_id: "entry2",
        name: "Second",
        printer: snapshot({ print_state: "idle" }),
      }),
    },
    config: { fleet: true },
  });
  assert.deepEqual(texts(card, ".name"), ["First", "Second"]);
});

test("an empty fleet renders a helpful message rather than nothing", async () => {
  const { card } = await mountCard({ printers: [], descriptions: {} });
  assert.match(card.shadowRoot.querySelector(".empty").textContent, /No 3D printer/);
});

test("a printer whose description fails still renders from the list, without controls", async () => {
  const { card } = await mountCard({
    printers: [{ entry_id: "entry1", name: "Printer", model: "Centauri Carbon" }],
    descriptions: {},
  });
  assert.deepEqual(texts(card, ".name"), ["Printer"]);
  assert.equal(card.shadowRoot.querySelector(".ctl"), null);
  assert.ok(card.shadowRoot.querySelector(".warning"), "expected an unreachable warning");
});

test("a failing websocket api is reported instead of rendering a blank card", async () => {
  const { card } = await mountCard({ failList: true });
  const empty = card.shadowRoot.querySelector(".empty");
  assert.ok(empty, "expected an error message");
  assert.match(empty.textContent, /integration is not loaded/);
});
