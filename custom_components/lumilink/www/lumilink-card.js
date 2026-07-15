/*  LumiLink Card – bundled with the LumiLink integration.
 *  A resizable pool-light control card: lamp visual, on/off, 11 colours,
 *  5 auto modes and a lamp-sync button.
 */

const CARD_VERSION = "1.4.0";

/* Colour hexes by effect-list index (11 fixed + 5 auto modes). */
const COLOR_HEX = [
  "#FFFFFF", "#0055FF", "#00EEFF", "#00BBAA", "#FF00BB", "#00BB00",
  "#FF6600", "#FFEE00", "#FFAA00", "#FF0000", "#FF88BB",
  "#7dd3e8", "#5db8d6", "#3d9dc4", "#2d82b2", "#1d67a0",
];
const AUTO_ICONS = ["🐢", "🚶", "🏃", "⚡", "💡"];
const NUM_FIXED = 11;

/* Escape text before interpolating into shadow-DOM HTML (names/effects are
 * config- and state-derived, so treat them as untrusted). */
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* Faithful SEAMAID LED Flachscheinwerfer (flat pool floodlight, ref 41474):
 * wide white ABS ring with grouped ventilation slits and small screw dimples,
 * a ground/faceted clear lens over a regular GRID of ~60 warm-white LEDs.
 * Off = white disc with pale LED grid; On = the lens glows the chosen colour
 * with a soft 120° halo. Live-updates to the selected effect colour. */
function lampSvg(color, on) {
  const C = 60;
  const RING_R = 57; // outer white ABS ring
  const LENS_R = 37; // clear lens radius

  const pt = (deg, r) => {
    const a = ((deg - 90) * Math.PI) / 180;
    return [C + r * Math.cos(a), C + r * Math.sin(a)];
  };

  // soft coloured halo + light bloom when on
  const glow = on
    ? `<circle cx="${C}" cy="${C}" r="58" fill="${color}" opacity="0.22">
         <animate attributeName="opacity" values="0.16;0.32;0.16" dur="3.4s" repeatCount="indefinite"/>
       </circle>
       <circle cx="${C}" cy="${C}" r="44" fill="${color}" opacity="0.28"/>`
    : "";

  // ventilation slit groups (6 around the ring) – 3 short bars each
  let vents = "";
  for (let g = 0; g < 6; g++) {
    const ang = g * 60 + 30;
    const [gx, gy] = pt(ang, 47);
    for (let b = -1; b <= 1; b++) {
      const [bx, by] = pt(ang, 47 + b * 3.1);
      vents += `<line x1="${bx.toFixed(1)}" y1="${(by - 3).toFixed(1)}"
                       x2="${bx.toFixed(1)}" y2="${(by + 3).toFixed(1)}"
                       stroke="#c4ccd1" stroke-width="1.3" stroke-linecap="round"/>`;
    }
  }

  // small recessed screw dimples on the ring (top + 3 lower positions)
  let screws = "";
  for (const ang of [0, 120, 210, 330]) {
    const [sx, sy] = pt(ang, 52);
    screws += `<circle cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="2.1"
                 fill="#eef1f3" stroke="#bcc6cc" stroke-width="1"/>`;
  }

  // regular square grid of LED dots clipped to the lens
  const lens = on ? color : "#dbe6ea";
  const inner = LENS_R - 4.5;
  let dots = "";
  const step = 7.0;
  for (let y = C - inner; y <= C + inner + 0.1; y += step) {
    for (let x = C - inner; x <= C + inner + 0.1; x += step) {
      if ((x - C) ** 2 + (y - C) ** 2 <= inner * inner) {
        dots += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.9"
                   fill="${on ? "#fffef5" : "#b3c0c6"}" opacity="${on ? 0.95 : 0.9}"/>`;
      }
    }
  }

  return `
  <svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
    ${glow}
    <circle cx="${C}" cy="${C}" r="${RING_R}" fill="var(--lumilink-ring, #f4f6f7)"
            stroke="#d3dade" stroke-width="1"/>
    <circle cx="${C}" cy="${C}" r="${RING_R - 2}" fill="none" stroke="#e6eaec" stroke-width="1"/>
    ${vents}
    ${screws}
    <circle cx="${C}" cy="${C}" r="${LENS_R + 3}" fill="#ffffff" stroke="#cfd7dc" stroke-width="1"/>
    <circle cx="${C}" cy="${C}" r="${LENS_R + 1}" fill="#aeb9bf"/>
    <circle cx="${C}" cy="${C}" r="${LENS_R}" fill="${lens}"/>
    <g>${dots}</g>
    ${
      on
        ? `<ellipse cx="47" cy="45" rx="15" ry="9" fill="#ffffff" opacity="0.22"
             transform="rotate(-28 47 45)"/>`
        : ""
    }
  </svg>`;
}

class LumiLinkCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("lumilink-card-editor");
  }

  static getStubConfig(hass) {
    const light =
      Object.keys(hass?.states || {}).find(
        (e) => e.startsWith("light.") && e.includes("lumilink")
      ) || Object.keys(hass?.states || {}).find((e) => e.startsWith("light.")) || "";
    const sync = Object.keys(hass?.states || {}).find(
      (e) => e.startsWith("button.") && (e.includes("lumilink") || e.includes("sync"))
    );
    const cfg = { entity: light };
    if (sync) cfg.sync_entity = sync;
    return cfg;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Bitte eine LumiLink light-Entity angeben (entity).");
    }
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 6;
  }

  /* Sections view: freely resizable, sensible defaults & minimums. */
  getGridOptions() {
    return { columns: 12, rows: "auto", min_columns: 6, min_rows: 4 };
  }

  /* Pre-2024.11 fallback for the older layout API. */
  getLayoutOptions() {
    return { grid_columns: 4, grid_rows: "auto", grid_min_columns: 2 };
  }

  _call(domain, service, data) {
    this._hass?.callService(domain, service, data);
  }

  _st() {
    return this._hass?.states?.[this._config?.entity];
  }

  _render() {
    if (!this._config || !this._hass) return;
    const st = this._st();
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }

    if (!st) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px">
        Entity <b>${esc(this._config.entity)}</b> nicht gefunden.</div></ha-card>`;
      return;
    }

    const name =
      this._config.name || st.attributes.friendly_name || "Pool Light";
    const available = st.state !== "unavailable";
    const on = st.state === "on";
    const effects = st.attributes.effect_list || [];
    const effect = st.attributes.effect;
    // -1 when the current effect isn't in the list (or none) – must NOT collapse
    // to 0, which would falsely mark white as selected.
    const idx = effects.indexOf(effect);
    const color = idx >= 0 && COLOR_HEX[idx] ? COLOR_HEX[idx] : "#FFFFFF";
    const busyRaw = st.attributes.busy_status;
    const busy = busyRaw ? (parseInt(busyRaw, 16) & 0x01) === 1 : false;
    const syncEntity = this._config.sync_entity;

    // Only render the LumiLink colour set (11 fixed) + auto modes (up to 5);
    // a generic light with more effects won't spawn spurious buttons.
    const swatches = effects
      .slice(0, NUM_FIXED)
      .map(
        (eff, i) => `
        <button class="sw ${i === idx && on ? "sel" : ""}" title="${esc(eff)}"
          data-effect="${esc(eff)}" style="--c:${COLOR_HEX[i]}"></button>`
      )
      .join("");

    const autos = effects
      .slice(NUM_FIXED, NUM_FIXED + AUTO_ICONS.length)
      .map(
        (eff, i) => `
        <button class="auto ${NUM_FIXED + i === idx && on ? "sel" : ""}"
          title="${esc(eff)}" data-effect="${esc(eff)}">
          ${AUTO_ICONS[i] || "✦"}<span>${esc(
          eff.replace(/^(→\s*)?Auto:\s*/, "")
        )}</span>
        </button>`
      )
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          height: 100%;
          display: flex; flex-direction: column;
          padding: 16px;
          box-sizing: border-box;
          overflow: hidden;
        }
        .topbar { display:flex; align-items:center; gap:8px; min-height:22px; }
        .badge {
          font-size:.72rem; padding:3px 9px; border-radius:10px;
          color:#fff; white-space:nowrap;
        }
        .badge.ok { background: var(--label-badge-green, #4caf50); }
        .badge.bad { background: var(--label-badge-red, #f44336); }
        .busy {
          width:8px; height:8px; border-radius:50%;
          background: var(--warning-color, #ffa600);
          animation: blink 1s infinite; display:${busy ? "block" : "none"};
        }
        @keyframes blink { 50% { opacity:.2 } }
        .stage {
          flex:1; display:flex; align-items:center; justify-content:center;
          min-height:130px; padding:10px 0 4px;
        }
        .lamp { width:min(58%, 220px); aspect-ratio:1; cursor:pointer;
          transition: transform .15s, filter .3s; }
        .lamp:hover { filter: brightness(1.04); }
        .lamp:active { transform: scale(.96); }
        .title {
          text-align:center; font-size:1.25rem; font-weight:600;
          color: var(--primary-text-color); margin-top:4px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        }
        .state {
          text-align:center; font-size:.9rem; color: var(--secondary-text-color);
          margin:2px 0 14px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        }
        .powers { display:flex; gap:10px; margin-bottom:14px; }
        .pw {
          flex:1; border:none; border-radius:14px; padding:12px 0;
          font-size:.95rem; font-weight:600; cursor:pointer;
          color:#fff; transition: filter .15s, transform .08s;
        }
        .pw:active { transform: scale(.97); }
        .pw[disabled] { opacity:.4; cursor:not-allowed; }
        .pw.on { background: var(--label-badge-green, #43a047); }
        .pw.off { background: var(--error-color, #b71c1c); }
        .pw:not(.active) { opacity:.55; }
        .pw.active { box-shadow: 0 0 0 2px color-mix(in srgb, currentColor 25%, transparent); }
        .pw:hover { filter: brightness(1.1); opacity:1; }
        .sect {
          font-size:.68rem; letter-spacing:.12em; text-transform:uppercase;
          color: var(--secondary-text-color); margin:2px 2px 8px;
        }
        .swatches {
          display:grid; grid-template-columns: repeat(auto-fit, minmax(34px, 1fr));
          gap:8px; margin-bottom:12px;
        }
        .sw {
          aspect-ratio:1; border-radius:50%; cursor:pointer;
          background: var(--c);
          border:2px solid var(--divider-color, rgba(127,127,127,.3));
          box-shadow: inset 0 -4px 8px rgba(0,0,0,.25);
          transition: transform .1s, border-color .15s, box-shadow .15s;
        }
        .sw:hover { transform: scale(1.12); }
        .sw.sel {
          border-color: var(--primary-color, #03a9f4);
          box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-color, #03a9f4) 40%, transparent),
                      inset 0 -4px 8px rgba(0,0,0,.25);
        }
        .autos {
          display:grid; grid-template-columns: repeat(auto-fit, minmax(86px, 1fr));
          gap:8px; margin-bottom:12px;
        }
        .auto {
          display:flex; align-items:center; justify-content:center; gap:6px;
          border-radius:12px; padding:8px 4px; cursor:pointer;
          font-size:.78rem; font-weight:600;
          color: var(--primary-text-color);
          background: var(--secondary-background-color, rgba(127,127,127,.12));
          border:1px solid var(--divider-color, rgba(127,127,127,.25));
          transition: background .15s;
        }
        .auto:hover { background: var(--divider-color, rgba(127,127,127,.25)); }
        .auto.sel {
          border-color: var(--primary-color, #03a9f4);
          background: color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent);
        }
        .footer { display:flex; align-items:center; gap:10px; }
        .sync {
          display:flex; align-items:center; gap:8px;
          border:none; border-radius:12px; padding:10px 16px;
          font-size:.85rem; font-weight:600; cursor:pointer;
          color:#fff; background: var(--primary-color, #0288d1);
        }
        .sync:hover { filter: brightness(1.1); }
        .status {
          flex:1; text-align:right; font-size:.8rem;
          color: var(--secondary-text-color);
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        }
      </style>
      <ha-card>
        <div class="topbar">
          <span class="badge ${available ? "ok" : "bad"}">
            ${available ? "Verbunden" : "Getrennt"}</span>
          <div class="busy" title="Modul beschäftigt"></div>
        </div>
        <div class="stage">
          <div class="lamp" id="lamp" title="${on ? "Ausschalten" : "Einschalten"}">
            ${lampSvg(color, on)}
          </div>
        </div>
        <div class="title">${esc(name)}</div>
        <div class="state">${
          !available
            ? "Nicht verbunden"
            : busy
            ? "Synchronisiere / arbeite …"
            : on
            ? esc(effect || "Eingeschaltet")
            : "Ausgeschaltet"
        }</div>
        <div class="powers">
          <button class="pw on ${on ? "active" : ""}" id="on" ${
      !available ? "disabled" : ""
    }>EIN</button>
          <button class="pw off ${!on ? "active" : ""}" id="off" ${
      !available ? "disabled" : ""
    }>AUS</button>
        </div>
        <div class="sect">Farben</div>
        <div class="swatches">${swatches}</div>
        ${autos ? `<div class="sect">Auto-Modi</div><div class="autos">${autos}</div>` : ""}
        ${
          syncEntity
            ? `<div class="footer"><button class="sync" id="sync"
                 title="Beide Lampen synchronisieren (Reset auf Weiß)">⟳ Lampen synchronisieren</button></div>`
            : ""
        }
      </ha-card>
    `;

    const root = this.shadowRoot;
    const eid = this._config.entity;
    root.getElementById("on")?.addEventListener("click", () =>
      this._call("light", "turn_on", { entity_id: eid })
    );
    root.getElementById("off")?.addEventListener("click", () =>
      this._call("light", "turn_off", { entity_id: eid })
    );
    root.getElementById("lamp")?.addEventListener("click", () =>
      this._call("light", on ? "turn_off" : "turn_on", { entity_id: eid })
    );
    root.getElementById("sync")?.addEventListener("click", () =>
      this._call("button", "press", { entity_id: syncEntity })
    );
    root.querySelectorAll(".sw, .auto").forEach((el) =>
      el.addEventListener("click", () =>
        this._call("light", "turn_on", {
          entity_id: eid,
          effect: el.dataset.effect,
        })
      )
    );
  }
}

/* Minimal visual editor built on ha-form. */
class LumiLinkCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) =>
        ({
          entity: "Licht-Entity (LumiLink)",
          sync_entity: "Sync-Button (optional)",
          name: "Name (optional)",
        }[s.name] || s.name);
      this._form.addEventListener("value-changed", (ev) => {
        this._config = { ...this._config, ...ev.detail.value };
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: this._config },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config || {};
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { domain: "light" } } },
      { name: "sync_entity", selector: { entity: { domain: "button" } } },
      { name: "name", selector: { text: {} } },
    ];
  }
}

if (!customElements.get("lumilink-card")) {
  customElements.define("lumilink-card", LumiLinkCard);
}
if (!customElements.get("lumilink-card-editor")) {
  customElements.define("lumilink-card-editor", LumiLinkCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "lumilink-card")) {
  window.customCards.push({
    type: "lumilink-card",
    name: "LumiLink Card",
    description:
      "Pool-Licht-Steuerung für SEAMAID LumiLink: Ein/Aus, 16 Farben/Modi, Lampen-Sync.",
    preview: true,
    documentationURL: "https://github.com/TimLuist1/ha-lumilink",
  });
}

console.info(
  `%c LUMILINK-CARD %c v${CARD_VERSION} `,
  "color:#03202e;background:#38e1ff;font-weight:700;border-radius:4px 0 0 4px;padding:2px 0",
  "color:#38e1ff;background:#03202e;font-weight:700;border-radius:0 4px 4px 0;padding:2px 0"
);
