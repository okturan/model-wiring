const stylesheet = new URL("./model-wiring-picker.css", import.meta.url).href;

// A credential that was checked and failed is not a usable credential.
const FAILED_PROBE_STATES = new Set(["expired", "unavailable", "policy_denied"]);

class ModelWiringPicker extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.state = {
      providers: [],
      profiles: [],
      activeProvider: null,
      providerChosen: false,
      hits: [],
      selected: null,
      currentProfiles: [],
      authRequired: false,
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

  set endpoint(value) {
    if (value == null) this.removeAttribute("endpoint");
    else this.setAttribute("endpoint", String(value));
  }

  get supportedProviders() {
    if (!this.hasAttribute("supported-providers")) return null;
    return new Set(
      (this.getAttribute("supported-providers") || "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
    );
  }

  async loadInitial() {
    this.setStatus("Discovering providers…");
    try {
      const [providers, profiles] = await Promise.all([
        this.request("/v1/providers"),
        this.request("/v1/profiles"),
      ]);
      this.state.profiles = profiles.items || [];
      this.state.providers = (providers.items || [])
        .map((provider) => ({ ...provider, surfaceState: this.providerState(provider) }))
        .sort((left, right) => this.providerOrder(left, right));
      this.renderProviders();

      const requestedModel = this.getAttribute("default-model") || "";
      const requestedProvider =
        this.getAttribute("default-provider") || requestedModel.split("/", 1)[0] || "";
      const initial =
        this.state.providers.find((provider) => provider.id === requestedProvider)
        || this.state.providers[0]
        || null;
      this.shadowRoot.querySelector("[data-query]").value =
        this.getAttribute("default-query") || "";
      if (initial) await this.activateProvider(initial, { focusModels: false });

      if (requestedModel) {
        const match = this.state.hits.find(
          (item) => item.model.qualified_id === requestedModel,
        );
        if (match) this.select(match);
      }
      this.setStatus(
        `${this.state.providers.length} providers · choose one to continue`,
      );
      this.dispatchEvent(
        new CustomEvent("model-wiring-picker-ready", {
          detail: {
            providers: this.state.providers.length,
            models: this.state.hits.length,
          },
          bubbles: true,
          composed: true,
        }),
      );
    } catch (error) {
      this.fail(error);
    }
  }

  bindEvents() {
    const root = this.shadowRoot;
    root.querySelector("[data-query]").addEventListener("input", () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.search(), 160);
    });
    root.querySelector("[data-back]").addEventListener("click", () => {
      this.state.providerChosen = false;
      this.updateStages();
      this.activeProviderButton()?.focus();
      this.setStatus("Choose a provider");
    });
    root.querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      this.resolve();
    });
    root.querySelector("[data-providers]").addEventListener("keydown", (event) => {
      if (this.moveListFocus(event, event.currentTarget)) return;
      if (!["Enter", "ArrowRight"].includes(event.key)) return;
      this.shadowRoot.activeElement?.click();
      event.preventDefault();
    });
    root.querySelector("[data-results]").addEventListener("keydown", (event) => {
      if (this.moveListFocus(event, event.currentTarget)) return;
      if (event.key === "ArrowLeft") {
        root.querySelector("[data-back]").click();
        event.preventDefault();
      }
    });
  }

  moveListFocus(event, collection) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return false;
    const buttons = [...collection.querySelectorAll("button:not([disabled])")];
    if (!buttons.length) return true;
    const current = buttons.indexOf(this.shadowRoot.activeElement);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    buttons[(current + delta + buttons.length) % buttons.length]?.focus();
    event.preventDefault();
    return true;
  }

  providerOrder(left, right) {
    const rank = { ready: 0, connect: 1, catalog: 2 };
    const preferred = this.preferredProvider();
    return (
      rank[left.surfaceState.state] - rank[right.surfaceState.state]
      || Number(right.id === preferred) - Number(left.id === preferred)
      || this.providerPopularity(left) - this.providerPopularity(right)
      || left.name.localeCompare(right.name)
      || left.id.localeCompare(right.id)
    );
  }

  providerPopularity(provider) {
    const rank = provider.metadata?.popularity_rank;
    return Number.isInteger(rank) && rank >= 0 ? rank : Number.MAX_SAFE_INTEGER;
  }

  preferredProvider() {
    return (
      this.getAttribute("recommended-provider")
      || (this.getAttribute("recommended-model") || "").split("/", 1)[0]
      || ""
    );
  }

  providerState(provider) {
    if (this.supportedProviders && !this.supportedProviders.has(provider.id)) {
      return { state: "catalog", label: "Catalogue only" };
    }
    const routes = this.accessRoutes(provider);
    if (!routes.length) {
      return { state: "catalog", label: "No known access route" };
    }
    const profiles = this.state.profiles.filter(
      (profile) => profile.provider_id === provider.id && profile.enabled,
    );
    const probeState = this.probeState(provider, profiles);
    if (FAILED_PROBE_STATES.has(probeState)) {
      // Checked and failed is not the same as configured.
      return {
        state: "connect",
        label: `Connect · credential ${probeState.replace("_", " ")}`,
      };
    }
    const credentialState = this.credentialState(provider, profiles);
    if (credentialState === "connectable") {
      return {
        state: "connect",
        label: `Connect · ${this.authLabel(routes[0].kind)}`,
        requires: this.requiredVariables(provider),
      };
    }
    return { state: "ready", label: "Ready now" };
  }

  accessRoutes(provider) {
    if (Array.isArray(provider.access_routes)) return provider.access_routes;
    // Older payloads only describe auth kinds; treat each as a bare route.
    return (provider.auth_methods || []).map((method) => ({
      kind: method.kind,
      billing_kind: (method.billing_kinds || [])[0] || "unknown",
      env: method.env || [],
      needs_credential: method.kind !== "anonymous",
    }));
  }

  requiredVariables(provider) {
    if (Array.isArray(provider.required_variables)) return provider.required_variables;
    return [...new Set(this.accessRoutes(provider).flatMap((route) => route.env || []))];
  }

  probeState(provider, profiles) {
    if (provider.probe_state) return provider.probe_state;
    // Best outcome any profile achieved: one dead key among working ones
    // does not make the provider unusable.
    const states = profiles
      .map((profile) => profile.metadata?.last_probe_state)
      .filter(Boolean);
    return ["ready", "unknown", "expired", "policy_denied", "unavailable"].find(
      (candidate) => states.includes(candidate),
    );
  }

  credentialState(provider, profiles) {
    if (provider.credential_state) return provider.credential_state;
    const routes = this.accessRoutes(provider);
    if (!routes.length) return "unavailable";
    if (profiles.length) return "configured";
    return routes.every((route) => !route.needs_credential)
      ? "configured"
      : "connectable";
  }

  async activateProvider(provider, { focusModels = true } = {}) {
    this.state.activeProvider = provider;
    this.state.providerChosen = focusModels;
    this.state.selected = null;
    this.state.currentProfiles = [];
    this.shadowRoot.querySelector("[data-config]").hidden = true;
    this.renderProviders();
    this.updateStages();
    await this.search();
    if (focusModels) {
      this.shadowRoot.querySelector("[data-query]").focus();
      this.setStatus(`${provider.name} · choose a model`);
    }
  }

  async search() {
    const provider = this.state.activeProvider;
    if (!provider) {
      this.state.hits = [];
      this.renderResults("Choose a provider first");
      return;
    }
    const query = this.shadowRoot.querySelector("[data-query]").value.trim();
    this.searchAbort?.abort();
    this.searchAbort = new AbortController();
    const params = new URLSearchParams({ q: query, limit: "30" });
    params.set("provider", provider.id);
    this.setStatus(`Loading ${provider.name} models…`);
    try {
      const payload = await this.request(`/v1/models?${params}`, {
        signal: this.searchAbort.signal,
      });
      this.state.hits = payload.items || [];
      this.renderResults(this.state.hits.length ? null : "No matching models");
      this.setStatus(`${this.state.hits.length} ${provider.name} models`);
    } catch (error) {
      if (error.name !== "AbortError") this.fail(error);
    }
  }

  select(hit) {
    this.state.selected = hit;
    this.state.providerChosen = true;
    this.shadowRoot.querySelectorAll("[data-results] button").forEach((button) => {
      button.setAttribute(
        "aria-selected",
        String(button.dataset.id === hit.model.qualified_id),
      );
    });
    const model = hit.model;
    this.setText("[data-selected-name]", model.name);
    this.setText("[data-selected-id]", model.qualified_id);
    this.setText(
      "[data-capabilities]",
      Object.entries(model.capabilities || {})
        .filter(([, enabled]) => enabled)
        .map(([name]) => name.replaceAll("_", " "))
        .join(" · ") || "None declared",
    );
    this.setText("[data-context]", this.formatNumber(model.limits?.context));
    this.fillSelect("[data-variant]", Object.keys(model.variants || {}), "Provider default");
    this.fillSelect("[data-effort]", model.reasoning_options || [], "Provider default");
    const provider = hit.provider;
    this.state.authRequired = this.accessRoutes(provider).some(
      (route) => route.needs_credential ?? route.kind !== "anonymous",
    );
    const tiers = [
      ...new Set([
        ...(provider.metadata?.tiers || []),
        ...(model.metadata?.tiers || []),
      ]),
    ];
    this.fillSelect("[data-tier]", tiers, "Provider default");
    const profiles = this.state.profiles.filter(
      (item) => item.provider_id === model.provider_id && item.enabled,
    );
    this.state.currentProfiles = profiles;
    this.renderProfiles(profiles);
    this.shadowRoot.querySelector("[data-config]").hidden = false;
    this.applyDefaults();
    this.updateReady();
    this.updateStages();
  }

  applyDefaults() {
    const defaults = [
      ["[data-variant]", "default-variant"],
      ["[data-effort]", "default-effort"],
      ["[data-tier]", "default-tier"],
    ];
    for (const [selector, attribute] of defaults) {
      const select = this.shadowRoot.querySelector(selector);
      const value = this.getAttribute(attribute);
      if (value && [...select.options].some((option) => option.value === value)) {
        select.value = value;
      }
    }
    const profile = this.getAttribute("default-profile");
    const profileSelect = this.shadowRoot.querySelector("[data-profile]");
    if (profile && [...profileSelect.options].some((option) => option.value === profile)) {
      profileSelect.value = profile;
      this.updateAccessDetails();
      this.updateReady();
    }
  }

  async resolve() {
    if (!this.state.selected) return;
    if (this.state.activeProvider?.surfaceState.state === "catalog") {
      this.setStatus("This provider is available for catalogue inspection only", true);
      return;
    }
    const value = (selector) => this.shadowRoot.querySelector(selector).value || null;
    const body = {
      model: this.state.selected.model.qualified_id,
      variant: value("[data-variant]"),
      effort: value("[data-effort]"),
      tier: value("[data-tier]"),
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
        new CustomEvent("model-wiring-selection", {
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
            <li data-stage="provider" class="active">Provider</li><li data-stage="model">Model</li><li data-stage="mode">Mode</li><li data-stage="access">Access</li><li data-stage="ready">Ready</li>
          </ol>
        </header>
        <section class="providers-stage" aria-label="Choose provider">
          <div class="stage-heading"><p class="eyebrow">PROVIDERS</p><span data-provider-count></span></div>
          <div data-providers class="providers" role="listbox" aria-label="AI providers"></div>
        </section>
        <section class="models-stage" aria-label="Choose model">
          <div class="stage-heading"><button data-back type="button" aria-label="Back to providers">← Providers</button><strong data-active-provider>Choose a provider</strong></div>
          <label class="search">Search this provider<input data-query type="search" placeholder="Model, family, or capability" autocomplete="off"></label>
          <div data-results class="results" role="listbox" aria-label="Provider models"></div>
        </section>
        <section data-config class="config" hidden>
          <p class="eyebrow">CHOSEN ROUTE</p>
          <h2 data-selected-name></h2>
          <code data-selected-id></code>
          <dl><dt>Capabilities</dt><dd data-capabilities></dd><dt>Context</dt><dd data-context></dd></dl>
          <div class="route-grid">
            <label>Variant<select data-variant></select></label>
            <label>Effort<select data-effort></select></label>
            <label>Tier<select data-tier></select></label>
            <label class="wide">Access route<select data-profile></select></label>
          </div>
          <dl class="access-facts"><dt>Authentication</dt><dd data-auth>Not selected</dd><dt>Economic path</dt><dd data-economic>Not selected</dd></dl>
          <button data-resolve class="resolve" type="submit" disabled>Use this route</button>
        </section>
        <footer><span class="privacy">No token material enters this control.</span><output data-status aria-live="polite"></output></footer>
      </form>`;
  }

  renderProviders() {
    const collection = this.shadowRoot.querySelector("[data-providers]");
    collection.replaceChildren();
    this.setText("[data-provider-count]", `${this.state.providers.length} available`);
    let previousState = null;
    const headings = {
      ready: "Ready now",
      connect: "Connect access",
      catalog: "Browse catalogue",
    };
    for (const provider of this.state.providers) {
      if (provider.surfaceState.state !== previousState) {
        const heading = document.createElement("p");
        heading.className = "provider-group";
        heading.textContent = headings[provider.surfaceState.state];
        collection.append(heading);
        previousState = provider.surfaceState.state;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.providerId = provider.id;
      button.dataset.state = provider.surfaceState.state;
      button.setAttribute("role", "option");
      button.setAttribute(
        "aria-selected",
        String(provider.id === this.state.activeProvider?.id),
      );
      const name = document.createElement("strong");
      name.textContent = provider.name;
      const count = document.createElement("span");
      count.textContent = `${provider.model_count} models`;
      const state = document.createElement("small");
      const requires = provider.surfaceState.requires || [];
      state.textContent = requires.length
        ? `${provider.surfaceState.label} · needs ${requires.join(", ")}`
        : provider.surfaceState.label;
      button.append(name, count, state);
      button.addEventListener("click", () => this.activateProvider(provider));
      collection.append(button);
    }
  }

  activeProviderButton() {
    const id = this.state.activeProvider?.id;
    return id
      ? this.shadowRoot.querySelector(`[data-provider-id="${CSS.escape(id)}"]`)
      : null;
  }

  renderResults(emptyMessage) {
    const results = this.shadowRoot.querySelector("[data-results]");
    results.replaceChildren();
    this.setText(
      "[data-active-provider]",
      this.state.activeProvider?.name || "Choose a provider",
    );
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
      id.textContent = hit.model.id;
      const badges = document.createElement("small");
      const capabilityBadges = Object.entries(hit.model.capabilities || {})
        .filter(([, enabled]) => enabled)
        .slice(0, 4)
        .map(([label]) => label.replaceAll("_", " "));
      if (hit.model.qualified_id === this.getAttribute("recommended-model")) {
        capabilityBadges.unshift("recommended");
      }
      badges.textContent = capabilityBadges.join(" · ");
      button.append(name, id, badges);
      button.addEventListener("click", () => this.select(hit));
      results.append(button);
    }
  }

  renderProfiles(profiles) {
    const select = this.shadowRoot.querySelector("[data-profile]");
    select.replaceChildren(new Option("Choose access route", ""));
    for (const profile of profiles) {
      const auth = this.authLabel(profile.auth_kind);
      const economics = this.billingLabel(profile.billing_kind);
      select.append(
        new Option(
          `${profile.account_label || profile.id} · ${auth} · ${economics}`,
          profile.id,
        ),
      );
    }
    if (profiles.length === 1) select.value = profiles[0].id;
    select.onchange = () => {
      this.updateAccessDetails();
      this.updateReady();
    };
    this.updateAccessDetails();
    this.updateReady();
  }

  updateAccessDetails() {
    const profileId = this.shadowRoot.querySelector("[data-profile]").value;
    const profile = this.state.currentProfiles.find((item) => item.id === profileId);
    this.setText("[data-auth]", profile ? this.authLabel(profile.auth_kind) : "Not selected");
    this.setText(
      "[data-economic]",
      profile ? this.billingLabel(profile.billing_kind) : "Not selected",
    );
  }

  updateReady() {
    if (!this.state.selected) return;
    const profile = this.shadowRoot.querySelector("[data-profile]").value;
    const supported = this.state.activeProvider?.surfaceState.state !== "catalog";
    const ready = supported && (!this.state.authRequired || Boolean(profile));
    this.shadowRoot.querySelector("[data-resolve]").disabled = !ready;
    this.updateStages(ready);
    const requires = this.state.activeProvider
      ? this.requiredVariables(this.state.activeProvider)
      : [];
    const connectHint = requires.length
      ? `Connect this provider with ${requires.join(", ")} or a stored credential`
      : "Choose an authenticated access route for this provider";
    this.setStatus(
      ready
        ? "Route ready; review and confirm"
        : supported
          ? connectHint
          : "Catalogue inspection only; this application cannot execute the provider",
    );
  }

  updateStages(ready = false) {
    const selected = Boolean(this.state.selected);
    const active = (stage, enabled) =>
      this.shadowRoot
        .querySelector(`[data-stage="${stage}"]`)
        .classList.toggle("active", enabled);
    active("provider", true);
    active("model", this.state.providerChosen || selected);
    active("mode", selected);
    active("access", ready);
    active("ready", ready);
    this.shadowRoot
      .querySelector(".models-stage")
      .classList.toggle("chosen", this.state.providerChosen);
  }

  authLabel(value) {
    return ({
      delegated: "Existing provider sign-in",
      api_key: "API key",
      credential_bundle: "Credentials",
      oauth: "OAuth sign-in",
      anonymous: "No sign-in",
    })[value] || String(value || "Not selected").replaceAll("_", " ");
  }

  billingLabel(value) {
    return ({
      subscription: "Subscription access",
      api: "Metered API usage",
      marketplace: "Marketplace credits",
      local: "Local compute",
    })[value] || String(value || "Not selected").replaceAll("_", " ");
  }

  fillSelect(selector, values, emptyLabel) {
    const select = this.shadowRoot.querySelector(selector);
    select.replaceChildren(new Option(emptyLabel, ""));
    for (const value of values) select.append(new Option(value, value));
  }

  setText(selector, value) {
    this.shadowRoot.querySelector(selector).textContent = value;
  }

  setStatus(message, error = false) {
    this.state.error = error ? message : null;
    const output = this.shadowRoot.querySelector("[data-status]");
    output.classList.toggle("error", error);
    output.textContent = message;
  }

  fail(error) {
    const message = error instanceof Error ? error.message : String(error);
    this.setStatus(message, true);
  }

  async request(path, options = {}) {
    const response = await fetch(`${this.endpoint}${path}`, options);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || `Request failed (${response.status})`);
    }
    return payload;
  }

  formatNumber(value) {
    return value == null ? "Not declared" : new Intl.NumberFormat().format(value);
  }
}

customElements.define("model-wiring-picker", ModelWiringPicker);

export { ModelWiringPicker };
