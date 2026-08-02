const stylesheet = new URL("./model-provider-picker.css", import.meta.url).href;

class ModelProviderPicker extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.state = {
      providers: [],
      profiles: [],
      hits: [],
      selected: null,
      authRequired: false,
      loading: false,
      error: null,
    };
    this.searchTimer = null;
    this.searchAbort = null;
  }

  connectedCallback() {
    this.renderShell();
    this.bindEvents();
    this.loadInitial();
  }

  get endpoint() {
    return (this.getAttribute("endpoint") || "").replace(/\/$/, "");
  }

  async loadInitial() {
    this.setStatus("Loading providers…");
    try {
      const [providers, profiles] = await Promise.all([
        this.request("/v1/providers"),
        this.request("/v1/profiles"),
      ]);
      this.state.providers = providers.items || [];
      this.state.profiles = profiles.items || [];
      this.renderProviders();
      this.setStatus(`${this.state.providers.length} providers ready`);
      this.search();
    } catch (error) {
      this.fail(error);
    }
  }

  bindEvents() {
    const root = this.shadowRoot;
    root.querySelector("[data-provider]").addEventListener("change", () => this.search());
    root.querySelector("[data-query]").addEventListener("input", () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.search(), 160);
    });
    root.querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      this.resolve();
    });
    root.querySelector("[data-results]").addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const buttons = [...event.currentTarget.querySelectorAll("button")];
      const current = buttons.indexOf(this.shadowRoot.activeElement);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      buttons[(current + delta + buttons.length) % buttons.length]?.focus();
      event.preventDefault();
    });
  }

  async search() {
    const query = this.shadowRoot.querySelector("[data-query]").value.trim();
    const provider = this.shadowRoot.querySelector("[data-provider]").value;
    if (!query) {
      this.state.hits = [];
      this.renderResults("Type to search the catalog");
      return;
    }
    this.searchAbort?.abort();
    this.searchAbort = new AbortController();
    const params = new URLSearchParams({ q: query, limit: "30" });
    if (provider) params.set("provider", provider);
    this.setStatus("Searching…");
    try {
      const payload = await this.request(`/v1/models?${params}`, {
        signal: this.searchAbort.signal,
      });
      this.state.hits = payload.items || [];
      this.renderResults(this.state.hits.length ? null : "No matching models");
      this.setStatus(`${this.state.hits.length} models found`);
    } catch (error) {
      if (error.name !== "AbortError") this.fail(error);
    }
  }

  select(hit) {
    this.state.selected = hit;
    this.shadowRoot.querySelectorAll("[data-results] button").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.id === hit.model.qualified_id));
    });
    const model = hit.model;
    this.setText("[data-selected-name]", model.name);
    this.setText("[data-selected-id]", model.qualified_id);
    this.setText(
      "[data-capabilities]",
      Object.entries(model.capabilities || {})
        .filter(([, enabled]) => enabled)
        .map(([name]) => name)
        .join(" · ") || "None declared",
    );
    this.setText("[data-context]", this.formatNumber(model.limits?.context));
    this.fillSelect("[data-variant]", Object.keys(model.variants || {}), "Provider default");
    this.fillSelect("[data-effort]", model.reasoning_options || [], "Provider default");
    const provider = hit.provider;
    this.state.authRequired = (provider.auth_methods || []).length > 0;
    const tiers = [...new Set([...(provider.metadata?.tiers || []), ...(model.metadata?.tiers || [])])];
    this.fillSelect("[data-tier]", tiers, "Provider default");
    const profiles = this.state.profiles.filter((item) => item.provider_id === model.provider_id && item.enabled);
    this.fillSelect(
      "[data-billing]",
      [...new Set(profiles.map((item) => item.billing_kind))],
      "Unresolved",
    );
    this.renderProfiles(profiles);
    this.shadowRoot.querySelector("[data-config]").hidden = false;
    this.updateReady();
  }

  async resolve() {
    if (!this.state.selected) return;
    const value = (selector) => this.shadowRoot.querySelector(selector).value || null;
    const body = {
      model: this.state.selected.model.qualified_id,
      variant: value("[data-variant]"),
      effort: value("[data-effort]"),
      tier: value("[data-tier]"),
      billing_kind: value("[data-billing]"),
      credential_profile: value("[data-profile]"),
    };
    this.setStatus("Validating route…");
    try {
      const plan = await this.request("/v1/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      this.setStatus(`Ready: ${plan.qualified_model}`);
      this.dispatchEvent(
        new CustomEvent("model-provider-selection", {
          detail: plan,
          bubbles: true,
          composed: true,
        }),
      );
    } catch (error) {
      this.fail(error);
    }
  }

  renderShell() {
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${stylesheet}">
      <form class="picker">
        <header>
          <p class="eyebrow">MODEL ROUTE</p>
          <ol class="flow" aria-label="Selection progress">
            <li class="active">Discover</li><li data-stage="model">Model</li><li data-stage="mode">Mode</li><li data-stage="billing">Billing</li><li data-stage="ready">Ready</li>
          </ol>
        </header>
        <section class="discover" aria-label="Discover models">
          <label>Provider<select data-provider><option value="">All providers</option></select></label>
          <label class="search">Search<input data-query type="search" placeholder="Model, family, or provider" autocomplete="off"></label>
          <div data-results class="results" role="listbox" aria-label="Matching models"></div>
        </section>
        <aside data-config class="config" hidden>
          <p class="eyebrow">SELECTION</p>
          <h2 data-selected-name></h2>
          <code data-selected-id></code>
          <dl><dt>Capabilities</dt><dd data-capabilities></dd><dt>Context</dt><dd data-context></dd></dl>
          <div class="route-grid">
            <label>Variant<select data-variant></select></label>
            <label>Effort<select data-effort></select></label>
            <label>Tier<select data-tier></select></label>
            <label>Billing<select data-billing></select></label>
            <label class="wide">Credential profile<select data-profile></select></label>
          </div>
          <button data-resolve class="resolve" type="submit" disabled>Use this route</button>
        </aside>
        <footer><span class="privacy">No token material enters this control.</span><output data-status aria-live="polite"></output></footer>
      </form>`;
  }

  renderProviders() {
    const select = this.shadowRoot.querySelector("[data-provider]");
    for (const provider of this.state.providers) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = `${provider.name} (${provider.model_count})`;
      select.append(option);
    }
  }

  renderResults(emptyMessage) {
    const results = this.shadowRoot.querySelector("[data-results]");
    results.replaceChildren();
    if (emptyMessage) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = emptyMessage;
      results.append(empty);
      return;
    }
    for (const hit of this.state.hits) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.id = hit.model.qualified_id;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", "false");
      const name = document.createElement("strong");
      name.textContent = hit.model.name;
      const id = document.createElement("span");
      id.textContent = hit.model.qualified_id;
      const badges = document.createElement("small");
      badges.textContent = Object.entries(hit.model.capabilities || {})
        .filter(([, enabled]) => enabled)
        .slice(0, 4)
        .map(([label]) => label.replaceAll("_", " "))
        .join(" · ");
      button.append(name, id, badges);
      button.addEventListener("click", () => this.select(hit));
      results.append(button);
    }
  }

  renderProfiles(profiles) {
    const billing = this.shadowRoot.querySelector("[data-billing]");
    const render = () => {
      const candidates = profiles.filter((item) => !billing.value || item.billing_kind === billing.value);
      const select = this.shadowRoot.querySelector("[data-profile]");
      select.replaceChildren(new Option("Choose explicitly", ""));
      for (const profile of candidates) {
        select.append(new Option(profile.account_label || profile.id, profile.id));
      }
      if (candidates.length === 1) select.value = candidates[0].id;
      this.updateReady();
    };
    billing.onchange = render;
    this.shadowRoot.querySelector("[data-profile]").onchange = () => this.updateReady();
    render();
  }

  updateReady() {
    if (!this.state.selected) return;
    const profile = this.shadowRoot.querySelector("[data-profile]").value;
    const ready = !this.state.authRequired || Boolean(profile);
    this.shadowRoot.querySelector("[data-resolve]").disabled = !ready;
    for (const stage of ["model", "mode"]) {
      this.shadowRoot.querySelector(`[data-stage="${stage}"]`).classList.add("active");
    }
    for (const stage of ["billing", "ready"]) {
      this.shadowRoot
        .querySelector(`[data-stage="${stage}"]`)
        .classList.toggle("active", ready);
    }
    this.setStatus(
      ready
        ? "Route ready; review and confirm"
        : "Credential profile setup is required for this provider",
    );
  }

  fillSelect(selector, values, emptyLabel) {
    const select = this.shadowRoot.querySelector(selector);
    select.replaceChildren(new Option(emptyLabel, ""));
    for (const value of values) select.append(new Option(value, value));
  }

  setText(selector, value) {
    this.shadowRoot.querySelector(selector).textContent = value;
  }

  setStatus(message) {
    this.state.error = null;
    const output = this.shadowRoot.querySelector("[data-status]");
    output.classList.remove("error");
    output.textContent = message;
  }

  fail(error) {
    const message = error instanceof Error ? error.message : String(error);
    this.state.error = message;
    const output = this.shadowRoot.querySelector("[data-status]");
    output.classList.add("error");
    output.textContent = message;
  }

  async request(path, options = {}) {
    const response = await fetch(`${this.endpoint}${path}`, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message || `Request failed (${response.status})`);
    return payload;
  }

  formatNumber(value) {
    return value == null ? "Not declared" : new Intl.NumberFormat().format(value);
  }
}

customElements.define("model-provider-picker", ModelProviderPicker);

export { ModelProviderPicker };
