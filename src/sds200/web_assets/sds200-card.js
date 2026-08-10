const SDS200_CARD_TYPE = "sds200-card";
const SDS200_CARD_TAG = "sds200-card";

const SDS200_ENTITY_FIELDS = Object.freeze([
  Object.freeze({
    key: "scanner_connected",
    label: "Scanner connection",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "system",
    label: "System",
    domain: "sensor",
  }),
  Object.freeze({
    key: "department",
    label: "Department",
    domain: "sensor",
  }),
  Object.freeze({
    key: "channel",
    label: "Channel",
    domain: "sensor",
  }),
  Object.freeze({
    key: "signal",
    label: "Signal",
    domain: "sensor",
  }),
  Object.freeze({
    key: "rssi",
    label: "RSSI",
    domain: "sensor",
  }),
  Object.freeze({
    key: "audio_running",
    label: "Audio",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "recording_active",
    label: "Recording",
    domain: "binary_sensor",
  }),
  Object.freeze({
    key: "recording_status",
    label: "Recording status",
    domain: "sensor",
  }),
  Object.freeze({
    key: "daemon_state",
    label: "Daemon state",
    domain: "sensor",
  }),
]);

function fieldForName(name) {
  return SDS200_ENTITY_FIELDS.find(
    (field) => field.key === name,
  );
}

function requireCardConfig(config) {
  if (
    config === null ||
    typeof config !== "object" ||
    Array.isArray(config)
  ) {
    throw new Error(
      "SDS200 card configuration must be an object.",
    );
  }

  const title =
    typeof config.title === "string" && config.title.trim()
      ? config.title.trim()
      : "SDS200 Scanner";

  const source =
    config.entities === undefined ? {} : config.entities;

  if (
    source === null ||
    typeof source !== "object" ||
    Array.isArray(source)
  ) {
    throw new Error(
      "SDS200 card entities must be an object.",
    );
  }

  const entities = {};

  for (const { key, domain } of SDS200_ENTITY_FIELDS) {
    const value = source[key];

    if (
      value === undefined ||
      value === null ||
      value === ""
    ) {
      continue;
    }

    if (
      typeof value !== "string" ||
      value.trim() !== value ||
      !value.includes(".")
    ) {
      throw new Error(
        `SDS200 card entity "${key}" must be a Home Assistant entity ID.`,
      );
    }

    if (!value.startsWith(`${domain}.`)) {
      throw new Error(
        `SDS200 card entity "${key}" must be in the ${domain} domain.`,
      );
    }

    entities[key] = value;
  }

  return Object.freeze({
    title,
    entities: Object.freeze(entities),
  });
}

function textElement(
  documentObject,
  tagName,
  className,
  text,
) {
  const node = documentObject.createElement(tagName);
  node.className = className;
  node.textContent = text;
  return node;
}

class Sds200Card extends HTMLElement {
  static getStubConfig() {
    return {
      entities: {},
    };
  }

  static getConfigForm() {
    return {
      schema: [
        {
          name: "title",
          selector: {
            text: {},
          },
        },
        {
          type: "expandable",
          name: "entities",
          title: "SDS200 entities",
          schema: SDS200_ENTITY_FIELDS.map(
            ({ key, domain }) => ({
              name: key,
              selector: {
                entity: {
                  filter: [
                    {
                      domain,
                    },
                  ],
                },
              },
            }),
          ),
        },
      ],
      computeLabel: (schema) => {
        if (schema.name === "title") {
          return "Title";
        }

        const field = fieldForName(schema.name);
        return field?.label;
      },
      computeHelper: (schema) => {
        if (schema.name === "entities") {
          return (
            "Select the entities created by the SDS200 " +
            "MQTT Discovery device."
          );
        }

        return undefined;
      },
      assertConfig: (config) => {
        requireCardConfig(config);
      },
    };
  }

  constructor() {
    super();

    this._config = null;
    this._states = {};
    this._unsubscribe = null;

    this._updateStates = this._updateStates.bind(this);

    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    if (this._unsubscribe !== null) {
      return;
    }

    const event = new CustomEvent(
      "context-request",
      {
        bubbles: true,
        composed: true,
        cancelable: true,
      },
    );

    event.context = "states";
    event.subscribe = true;
    event.callback = this._updateStates;

    this.dispatchEvent(event);
    this._render();
  }

  disconnectedCallback() {
    if (typeof this._unsubscribe === "function") {
      this._unsubscribe();
    }

    this._unsubscribe = null;
  }

  setConfig(config) {
    this._config = requireCardConfig(config);
    this._render();
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return {
      rows: 4,
      columns: 6,
      min_rows: 3,
      min_columns: 3,
    };
  }

  _updateStates(states, unsubscribe) {
    this._states =
      states !== null && typeof states === "object"
        ? states
        : {};

    this._unsubscribe =
      typeof unsubscribe === "function"
        ? unsubscribe
        : null;

    this._render();
  }

  _state(field) {
    const entityId = this._config?.entities[field];

    if (!entityId) {
      return null;
    }

    return this._states[entityId] ?? null;
  }

  _stateText(field, fallback = "—") {
    const stateObject = this._state(field);

    if (!stateObject) {
      return fallback;
    }

    const value = stateObject.state;

    if (
      typeof value !== "string" ||
      value === "" ||
      value === "unknown" ||
      value === "unavailable"
    ) {
      return fallback;
    }

    return value;
  }

  _binaryActive(field) {
    const value =
      this._stateText(field, "").toLowerCase();

    return (
      value === "on" ||
      value === "true" ||
      value === "running"
    );
  }

  _renderStatus(
    documentObject,
    field,
    label,
  ) {
    const wrapper =
      documentObject.createElement("div");

    wrapper.className = "status";

    const indicator =
      documentObject.createElement("span");

    indicator.className = "status-indicator";
    indicator.dataset.active = String(
      this._binaryActive(field),
    );
    indicator.setAttribute(
      "aria-hidden",
      "true",
    );

    wrapper.append(
      indicator,
      textElement(
        documentObject,
        "span",
        "status-label",
        label,
      ),
    );

    return wrapper;
  }

  _renderRow(
    documentObject,
    field,
    label,
  ) {
    const row =
      documentObject.createElement("div");

    row.className = "row";

    row.append(
      textElement(
        documentObject,
        "dt",
        "label",
        label,
      ),
      textElement(
        documentObject,
        "dd",
        "value",
        this._stateText(field),
      ),
    );

    return row;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const documentObject = this.ownerDocument;

    const style =
      documentObject.createElement("style");

    style.textContent = `
      :host {
        display: block;
        color: var(--primary-text-color, #1f2933);
      }

      .card {
        overflow: hidden;
        border: 1px solid var(--divider-color, rgb(127 127 127 / 0.25));
        border-radius: var(--ha-card-border-radius, 12px);
        background: var(--ha-card-background, var(--card-background-color, #fff));
        box-shadow: var(--ha-card-box-shadow, none);
      }

      .header {
        padding: 1rem 1rem 0.75rem;
        border-bottom: 1px solid var(--divider-color, rgb(127 127 127 / 0.2));
      }

      .title {
        margin: 0;
        font-size: 1.1rem;
        font-weight: 600;
      }

      .channel {
        margin: 0.4rem 0 0;
        font-size: 1.35rem;
        font-weight: 700;
        overflow-wrap: anywhere;
      }

      .hierarchy {
        margin: 0.25rem 0 0;
        color: var(--secondary-text-color, #687078);
        font-size: 0.9rem;
        overflow-wrap: anywhere;
      }

      .statuses {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem 1rem;
        padding: 0.75rem 1rem;
        border-bottom: 1px solid var(--divider-color, rgb(127 127 127 / 0.2));
      }

      .status {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        font-size: 0.82rem;
      }

      .status-indicator {
        width: 0.55rem;
        height: 0.55rem;
        border-radius: 50%;
        background: var(--disabled-text-color, #9aa0a6);
      }

      .status-indicator[data-active="true"] {
        background: var(--success-color, #43a047);
      }

      .details {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin: 0;
        padding: 0.75rem 1rem 1rem;
        gap: 0.6rem 1rem;
      }

      .row {
        min-width: 0;
      }

      .label {
        margin: 0;
        color: var(--secondary-text-color, #687078);
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      .value {
        margin: 0.16rem 0 0;
        font-size: 0.95rem;
        overflow-wrap: anywhere;
      }

      .configuration {
        margin: 0;
        padding: 0.8rem 1rem 1rem;
        color: var(--secondary-text-color, #687078);
        font-size: 0.85rem;
      }

      @media (max-width: 28rem) {
        .details {
          grid-template-columns: 1fr;
        }
      }
    `;

    const article =
      documentObject.createElement("article");

    article.className = "card";

    const header =
      documentObject.createElement("header");

    header.className = "header";

    header.append(
      textElement(
        documentObject,
        "h2",
        "title",
        this._config.title,
      ),
      textElement(
        documentObject,
        "p",
        "channel",
        this._stateText(
          "channel",
          "No channel configured",
        ),
      ),
    );

    const hierarchy = [
      this._stateText("system", ""),
      this._stateText("department", ""),
    ].filter(Boolean);

    if (hierarchy.length) {
      header.append(
        textElement(
          documentObject,
          "p",
          "hierarchy",
          hierarchy.join(" · "),
        ),
      );
    }

    article.append(header);

    const statuses =
      documentObject.createElement("div");

    statuses.className = "statuses";

    statuses.append(
      this._renderStatus(
        documentObject,
        "scanner_connected",
        "Scanner",
      ),
      this._renderStatus(
        documentObject,
        "audio_running",
        "Audio",
      ),
      this._renderStatus(
        documentObject,
        "recording_active",
        "Recording",
      ),
    );

    article.append(statuses);

    const details =
      documentObject.createElement("dl");

    details.className = "details";

    for (const [field, label] of [
      ["signal", "Signal"],
      ["rssi", "RSSI"],
      ["recording_status", "Recording status"],
      ["daemon_state", "Daemon"],
    ]) {
      details.append(
        this._renderRow(
          documentObject,
          field,
          label,
        ),
      );
    }

    article.append(details);

    if (
      Object.keys(
        this._config.entities,
      ).length === 0
    ) {
      article.append(
        textElement(
          documentObject,
          "p",
          "configuration",
          "Select the SDS200 entities in the card editor.",
        ),
      );
    }

    this.shadowRoot.replaceChildren(
      style,
      article,
    );
  }
}

if (!customElements.get(SDS200_CARD_TAG)) {
  customElements.define(
    SDS200_CARD_TAG,
    Sds200Card,
  );
}

window.customCards =
  window.customCards || [];

if (
  !window.customCards.some(
    (card) =>
      card.type === SDS200_CARD_TYPE,
  )
) {
  window.customCards.push({
    type: SDS200_CARD_TYPE,
    name: "SDS200 Scanner",
    description:
      "Read-only scanner status from the sds200 Home Assistant entities.",
    preview: true,
    documentationURL:
      "https://github.com/stevenboyd78/sds200-python/blob/main/docs/home-assistant-app.md",
  });
}
