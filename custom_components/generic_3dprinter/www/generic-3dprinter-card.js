/**
 * Generic 3D Printer Controller card.
 *
 * Renders one printer, or a fleet, from the integration's own WebSocket API. The
 * card never learns which protocol a printer speaks: it reads the capability array
 * the backend reports and draws a control per capability present, so a printer that
 * cannot pause simply has no pause button and a printer with no camera has no
 * camera pane.
 *
 * Ships with the integration, so `custom:generic-3dprinter-card` is available as
 * soon as the integration is installed.
 */

const CARD_VERSION = "0.1.0";

const WS_LIST = "generic_3dprinter/list";
const WS_DESCRIBE = "generic_3dprinter/describe";
const WS_SEND = "generic_3dprinter/send";

/* How often signed URLs and the printer snapshot are refreshed. Signed URLs live
 * for six hours, so this is about keeping the displayed state honest, not about
 * staying ahead of an expiry. */
const REFRESH_MS = 5000;

const STATE_COLORS = {
  idle: "var(--state-inactive-color, #9e9e9e)",
  preparing: "var(--warning-color, #ff9800)",
  printing: "var(--state-active-color, #4caf50)",
  paused: "var(--warning-color, #ff9800)",
  finished: "var(--info-color, #2196f3)",
  cancelled: "var(--state-inactive-color, #9e9e9e)",
  error: "var(--error-color, #f44336)",
  unknown: "var(--disabled-text-color, #bdbdbd)",
};

const STATE_LABELS = {
  idle: "Idle",
  preparing: "Preparing",
  printing: "Printing",
  paused: "Paused",
  finished: "Finished",
  cancelled: "Cancelled",
  error: "Error",
  unknown: "Unknown",
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const asNumber = (value) =>
  value === null || value === undefined || value === "" ? null : Number(value);

const formatTemperature = (value) => {
  const number = asNumber(value);
  return number === null ? "—" : `${number.toFixed(0)} °C`;
};

const formatDuration = (seconds) => {
  const total = asNumber(seconds);
  if (total === null || total < 0) return "—";
  const whole = Math.round(total);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(whole % 60).padStart(2, "0")}s`;
  return `${whole}s`;
};

const formatBytes = (value) => {
  const bytes = asNumber(value);
  if (bytes === null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  let size = bytes;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
};

class Generic3DPrinterCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._printers = [];
    this._descriptions = new Map();
    this._timer = null;
    this._busy = new Set();
    this._expanded = null;
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._start();
    else this._render();
  }

  connectedCallback() {
    if (this._hass) this._start();
  }

  disconnectedCallback() {
    this._stop();
  }

  getCardSize() {
    return this._config.fleet ? Math.max(3, this._printers.length * 2) : 6;
  }

  _start() {
    if (this._timer) return;
    this._refreshAll();
    this._timer = window.setInterval(() => this._refreshAll(), REFRESH_MS);
  }

  _stop() {
    if (this._timer) {
      window.clearInterval(this._timer);
      this._timer = null;
    }
  }

  async _refreshAll() {
    if (!this._hass) return;
    try {
      const result = await this._hass.callWS({ type: WS_LIST });
      this._printers = result.printers || [];
    } catch (err) {
      this._printers = [];
      this._error = err.message || String(err);
    }

    const wanted = this._config.entry_id
      ? this._printers.filter((item) => item.entry_id === this._config.entry_id)
      : this._config.fleet
        ? this._printers
        : this._printers.slice(0, 1);

    await Promise.all(
      wanted.map(async (printer) => {
        try {
          const description = await this._hass.callWS({
            type: WS_DESCRIBE,
            entry_id: printer.entry_id,
          });
          this._descriptions.set(printer.entry_id, description);
        } catch (err) {
          this._descriptions.set(printer.entry_id, { error: err.message || String(err) });
        }
      }),
    );
    this._visible = wanted;
    this._render();
  }

  async _send(entryId, command, data = {}) {
    const key = `${entryId}:${command}`;
    this._busy.add(key);
    this._render();
    try {
      await this._hass.callWS({
        type: WS_SEND,
        entry_id: entryId,
        command,
        data,
      });
    } catch (err) {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: err.message || String(err) },
          bubbles: true,
          composed: true,
        }),
      );
    } finally {
      this._busy.delete(key);
      await this._refreshAll();
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const style = this._style();
    const container = el("div", "card");

    if (this._config.title) {
      container.appendChild(el("div", "card-title", this._config.title));
    }

    if (this._error) {
      container.appendChild(el("div", "empty", `Cannot reach the integration: ${this._error}`));
    } else if (!this._visible || this._visible.length === 0) {
      container.appendChild(
        el("div", "empty", "No 3D printer yet. Add one from Settings, Devices and Services."),
      );
    } else {
      for (const printer of this._visible) {
        container.appendChild(this._renderPrinter(printer));
      }
    }

    this.shadowRoot.innerHTML = "";
    this.shadowRoot.appendChild(style);
    this.shadowRoot.appendChild(container);
  }

  _renderPrinter(printer) {
    const description = this._descriptions.get(printer.entry_id) || {};
    const snapshot = description.printer || {};
    const capabilities = snapshot.capabilities || [];
    const state = snapshot.print_state || "unknown";
    const busy = (command) => this._busy.has(`${printer.entry_id}:${command}`);

    const wrapper = el("div", "printer");
    wrapper.dataset.entryId = printer.entry_id;

    const header = el("div", "printer-header");
    const dot = el("span", "dot");
    dot.style.background = STATE_COLORS[state] || STATE_COLORS.unknown;
    header.appendChild(dot);

    const names = el("div", "names");
    names.appendChild(el("div", "name", description.name || printer.name));
    const subtitle = [
      description.model || printer.model || description.protocol || printer.protocol,
      description.firmware ? `fw ${description.firmware}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    if (subtitle) names.appendChild(el("div", "subtitle", subtitle));
    header.appendChild(names);

    const stateChip = el("div", "state", STATE_LABELS[state] || state);
    stateChip.style.color = STATE_COLORS[state] || STATE_COLORS.unknown;
    header.appendChild(stateChip);
    wrapper.appendChild(header);

    if (!description.connected) {
      wrapper.appendChild(
        el("div", "warning", description.last_error || "The printer is not answering."),
      );
    }

    const body = el("div", "body");
    const cameraColumn = this._renderCamera(description, capabilities);
    const infoColumn = el("div", "info");
    infoColumn.appendChild(this._renderProgress(snapshot, capabilities));
    infoColumn.appendChild(this._renderTemperatures(snapshot, capabilities));
    infoColumn.appendChild(this._renderJob(snapshot, capabilities));
    if (cameraColumn) {
      body.appendChild(cameraColumn);
      body.appendChild(infoColumn);
    } else {
      body.appendChild(infoColumn);
    }
    wrapper.appendChild(body);

    wrapper.appendChild(this._renderControls(printer.entry_id, capabilities, snapshot, busy));
    wrapper.appendChild(this._renderFooter(description, capabilities));
    return wrapper;
  }

  _renderCamera(description, capabilities) {
    if (!capabilities.includes("camera") || !description.snapshot_url) return null;
    const column = el("div", "camera");
    const image = el("img");
    image.src = description.snapshot_url;
    image.alt = "Printer camera";
    image.addEventListener("error", () => {
      image.replaceWith(el("div", "camera-error", "Camera unavailable"));
    });
    column.appendChild(image);
    return column;
  }

  _renderProgress(snapshot, capabilities) {
    const row = el("div", "progress-block");
    const percent = asNumber(snapshot.progress);
    if (percent !== null && capabilities.includes("pause")) {
      const track = el("div", "progress-track");
      const fill = el("div", "progress-fill");
      fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
      fill.style.background = STATE_COLORS[snapshot.print_state] || STATE_COLORS.printing;
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("div", "progress-label", `${percent.toFixed(0)}%`));
    }

    const stats = el("div", "stats");
    const layers =
      asNumber(snapshot.current_layer) !== null && asNumber(snapshot.total_layers) !== null
        ? `${snapshot.current_layer} / ${snapshot.total_layers}`
        : null;
    for (const [label, value] of [
      ["Layer", layers],
      ["Remaining", asNumber(snapshot.remaining) !== null ? formatDuration(snapshot.remaining) : null],
      ["Elapsed", asNumber(snapshot.elapsed) !== null ? formatDuration(snapshot.elapsed) : null],
      ["Speed", asNumber(snapshot.speed_factor) !== null ? `${snapshot.speed_factor}%` : null],
    ]) {
      if (value === null || value === undefined) continue;
      const cell = el("div", "stat");
      cell.appendChild(el("span", "stat-label", label));
      cell.appendChild(el("span", "stat-value", value));
      stats.appendChild(cell);
    }
    if (stats.childElementCount > 0) row.appendChild(stats);
    return row;
  }

  _renderTemperatures(snapshot, capabilities) {
    const grid = el("div", "temps");
    const entries = [
      ["Nozzle", snapshot.hotend, true],
      ["Bed", snapshot.bed, true],
      ["Chamber", snapshot.chamber, capabilities.includes("set_chamber_temp")],
    ];
    for (const [label, temps, visible] of entries) {
      if (!visible || !temps) continue;
      if (temps.current === null && temps.target === null) continue;
      const cell = el("div", "temp");
      cell.appendChild(el("span", "temp-label", label));
      const target = asNumber(temps.target);
      const current = formatTemperature(temps.current);
      cell.appendChild(
        el("span", "temp-value", target ? `${current} → ${target.toFixed(0)} °C` : current),
      );
      grid.appendChild(cell);
    }
    return grid;
  }

  _renderJob(snapshot, capabilities) {
    if (!snapshot.filename) return el("div", "hidden");
    const row = el("div", "job");
    row.appendChild(el("span", "job-label", "File"));
    row.appendChild(el("span", "job-name", snapshot.filename.split("/").pop()));
    return row;
  }

  _renderControls(entryId, capabilities, snapshot, busy) {
    const controls = el("div", "controls");
    const add = (label, command, data, enabled = true, accent = "") => {
      if (!capabilities.includes(command)) return;
      const button = el("button", `ctl ${accent}`, label);
      button.disabled = !enabled || busy(command);
      button.addEventListener("click", () => this._send(entryId, command, data));
      controls.appendChild(button);
    };

    const printing = snapshot.print_state === "printing";
    const paused = snapshot.print_state === "paused";

    add("Pause", "pause", {}, printing, "primary");
    add("Resume", "resume", {}, paused, "primary");
    add("Stop", "stop", {}, printing || paused, "danger");
    add("Home", "home", { axes: "XYZ" }, !printing && !paused);
    add("Light", "set_light", { on: true, channel: "chamber" }, true);
    add("Light off", "set_light", { on: false, channel: "chamber" }, true);

    if (controls.childElementCount === 0) return el("div", "hidden");
    return controls;
  }

  _renderFooter(description, capabilities) {
    const footer = el("div", "footer");
    if (capabilities.includes("web_ui") && description.web_ui_url) {
      const link = el("a", "link", "Open printer page");
      link.href = description.web_ui_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      footer.appendChild(link);
    }
    if (description.camera_stats && description.camera_stats.viewers) {
      footer.appendChild(
        el("span", "hint", `${description.camera_stats.viewers} watching`),
      );
    }
    if (footer.childElementCount === 0) return el("div", "hidden");
    return footer;
  }

  _style() {
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; }
      .card {
        background: var(--ha-card-background, var(--card-background-color, #fff));
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none);
        border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, #e0e0e0));
        padding: 16px;
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, sans-serif);
      }
      .card-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; }
      .printer + .printer { border-top: 1px solid var(--divider-color, #e0e0e0); margin-top: 14px; padding-top: 14px; }
      .printer-header { display: flex; align-items: center; gap: 10px; }
      .dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; }
      .names { flex: 1 1 auto; min-width: 0; }
      .name { font-weight: 600; }
      .subtitle { font-size: 0.78rem; color: var(--secondary-text-color); }
      .state { font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
      .warning { margin-top: 8px; font-size: 0.82rem; color: var(--error-color, #f44336); }
      .body { display: flex; gap: 14px; margin-top: 12px; flex-wrap: wrap; }
      .camera { flex: 1 1 220px; min-width: 180px; }
      .camera img { width: 100%; border-radius: 8px; display: block; background: #000; }
      .camera-error { font-size: 0.8rem; color: var(--secondary-text-color); }
      .info { flex: 2 1 260px; min-width: 200px; }
      .progress-track { height: 8px; border-radius: 4px; background: var(--divider-color, #e0e0e0); overflow: hidden; }
      .progress-fill { height: 100%; transition: width 0.4s ease; }
      .progress-label { font-size: 0.85rem; margin-top: 4px; font-variant-numeric: tabular-nums; }
      .stats { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; }
      .stat { display: flex; flex-direction: column; }
      .stat-label { font-size: 0.7rem; text-transform: uppercase; color: var(--secondary-text-color); letter-spacing: 0.04em; }
      .stat-value { font-variant-numeric: tabular-nums; }
      .temps { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; }
      .temp { display: flex; flex-direction: column; }
      .temp-label { font-size: 0.7rem; text-transform: uppercase; color: var(--secondary-text-color); letter-spacing: 0.04em; }
      .temp-value { font-variant-numeric: tabular-nums; }
      .job { display: flex; gap: 8px; margin-top: 10px; font-size: 0.85rem; }
      .job-label { color: var(--secondary-text-color); }
      .job-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .controls { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
      .ctl {
        border: 1px solid var(--divider-color, #e0e0e0);
        background: transparent;
        color: var(--primary-text-color);
        border-radius: 8px;
        padding: 7px 14px;
        font-size: 0.85rem;
        cursor: pointer;
      }
      .ctl:hover:not(:disabled) { background: var(--secondary-background-color, #f5f5f5); }
      .ctl:disabled { opacity: 0.4; cursor: default; }
      .ctl.primary { border-color: var(--primary-color); color: var(--primary-color); }
      .ctl.danger { border-color: var(--error-color, #f44336); color: var(--error-color, #f44336); }
      .footer { display: flex; gap: 12px; align-items: center; margin-top: 12px; font-size: 0.8rem; }
      .link { color: var(--primary-color); text-decoration: none; }
      .hint { color: var(--secondary-text-color); }
      .empty { color: var(--secondary-text-color); font-size: 0.9rem; }
      .hidden { display: none; }
      @media (max-width: 420px) { .body { flex-direction: column; } }
    `;
    return style;
  }
}

class Generic3DPrinterCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = "";
    const wrapper = document.createElement("div");
    wrapper.style.padding = "8px 0";

    const label = document.createElement("label");
    label.textContent = "Show the whole fleet";
    label.style.display = "flex";
    label.style.gap = "8px";
    label.style.alignItems = "center";

    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = Boolean(this._config && this._config.fleet);
    toggle.addEventListener("change", () => {
      this.dispatchEvent(
        new CustomEvent("config-changed", {
          detail: { config: { ...this._config, fleet: toggle.checked } },
          bubbles: true,
          composed: true,
        }),
      );
    });

    label.appendChild(toggle);
    wrapper.appendChild(label);
    this.shadowRoot.appendChild(wrapper);
  }
}

if (!customElements.get("generic-3dprinter-card")) {
  customElements.define("generic-3dprinter-card", Generic3DPrinterCard);
}
if (!customElements.get("generic-3dprinter-card-editor")) {
  customElements.define("generic-3dprinter-card-editor", Generic3DPrinterCardEditor);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "generic-3dprinter-card",
  name: "3D Printer",
  description:
    "Control a 3D printer or a whole fleet: state, progress, temperatures and camera. Draws only the controls the printer reports it supports.",
  preview: true,
  documentationURL: "https://github.com/dnviti/ha-generic-3dprinter-controller",
});

/* eslint-disable no-console */
console.info(
  `%c GENERIC-3DPRINTER-CARD %c ${CARD_VERSION} `,
  "color:#fff;background:#3f51b5;font-weight:700",
  "color:#3f51b5;background:#fff;font-weight:700",
);
