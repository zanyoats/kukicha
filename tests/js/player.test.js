const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const playerScript = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "kukicha", "static", "player.js"),
  "utf8"
);

class TestClassList {
  constructor(element) {
    this.element = element;
    this.names = new Set();
  }

  add(...names) {
    for (const name of names) {
      this.names.add(name);
    }
    this.sync();
  }

  remove(...names) {
    for (const name of names) {
      this.names.delete(name);
    }
    this.sync();
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : Boolean(force);
    if (enabled) {
      this.names.add(name);
    } else {
      this.names.delete(name);
    }
    this.sync();
    return enabled;
  }

  contains(name) {
    return this.names.has(name);
  }

  sync() {
    this.element.className = Array.from(this.names).join(" ");
  }
}

class TestElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.dataset = {};
    this.style = {
      values: new Map(),
      setProperty: (name, value) => {
        this.style.values.set(name, value);
      },
    };
    this.attributes = new Map();
    this.children = [];
    this.childNodes = this.children;
    this.listeners = new Map();
    this.classList = new TestClassList(this);
    this.className = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.indeterminate = false;
    this.value = "";
    this.max = "";
    this.type = "";
    this.name = "";
    this.href = "";
    this.src = "";
    this.alt = "";
    this.title = "";
    this.loading = "";
    this.open = false;
    this.queryResults = new Map();
    this.parentNode = null;
  }

  get elements() {
    return formAssociatedDescendants(this);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  click() {
    for (const listener of this.listeners.get("click") || []) {
      listener({target: this, preventDefault() {}});
    }
  }

  append(...nodes) {
    for (const node of nodes) {
      const index = this.children.indexOf(node);
      if (index !== -1) {
        this.children.splice(index, 1);
      }
      node.parentNode = this;
    }
    this.children.push(...nodes);
  }

  prepend(...nodes) {
    for (const node of nodes) {
      const index = this.children.indexOf(node);
      if (index !== -1) {
        this.children.splice(index, 1);
      }
      node.parentNode = this;
    }
    this.children.unshift(...nodes);
  }

  insertBefore(node, referenceNode) {
    const existingIndex = this.children.indexOf(node);
    if (existingIndex !== -1) {
      this.children.splice(existingIndex, 1);
    }
    const referenceIndex = this.children.indexOf(referenceNode);
    node.parentNode = this;
    if (referenceIndex === -1) {
      this.children.push(node);
      return node;
    }
    this.children.splice(referenceIndex, 0, node);
    return node;
  }

  replaceChildren(...nodes) {
    for (const child of this.children) {
      child.parentNode = null;
    }
    for (const node of nodes) {
      node.parentNode = this;
    }
    this.children.splice(0, this.children.length, ...nodes);
    this.textContent = "";
  }

  setQueryResult(selector, value) {
    this.queryResults.set(selector, value);
  }

  querySelector(selector) {
    const value = this.queryResults.get(selector);
    if (Array.isArray(value)) {
      return value[0] || null;
    }
    return value || findDescendant(this, selector);
  }

  querySelectorAll(selector) {
    const value = this.queryResults.get(selector);
    if (Array.isArray(value)) {
      return value;
    }
    return value ? [value] : findDescendants(this, selector);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "src") {
      this.src = String(value);
    }
  }

  getAttribute(name) {
    if (name === "src" && this.src) {
      return this.src;
    }
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  hasAttribute(name) {
    return this.attributes.has(name) || (name === "src" && Boolean(this.src));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "src") {
      this.src = "";
    }
  }

  remove() {
    if (!this.parentNode) {
      return;
    }
    const index = this.parentNode.children.indexOf(this);
    if (index !== -1) {
      this.parentNode.children.splice(index, 1);
    }
    this.parentNode = null;
  }

  after() {}

  focus() {}

  get isConnected() {
    return true;
  }

  closest() {
    return null;
  }

  matches() {
    return false;
  }

  scrollIntoView() {}

  getBoundingClientRect() {
    return {top: 0};
  }
}

function findDescendant(element, selector) {
  return findDescendants(element, selector)[0] || null;
}

function findDescendants(element, selector) {
  const matches = [];
  for (const child of element.children) {
    if (matchesSelector(child, selector)) {
      matches.push(child);
    }
    matches.push(...findDescendants(child, selector));
  }
  return matches;
}

function formAssociatedDescendants(element) {
  const items = [];
  for (const child of element.children) {
    if (
      child instanceof TestElement
      && ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(child.tagName)
    ) {
      items.push(child);
    }
    items.push(...formAssociatedDescendants(child));
  }
  return items;
}

function matchesSelector(element, selector) {
  if (selector.startsWith(".")) {
    return element.className.split(/\s+/).includes(selector.slice(1));
  }
  const dataMatch = selector.match(/^\[data-([a-z0-9-]+)(?:="([^"]*)")?\]$/i);
  if (dataMatch) {
    const key = datasetKey(dataMatch[1]);
    if (dataMatch[2] === undefined) {
      return Object.prototype.hasOwnProperty.call(element.dataset, key);
    }
    return String(element.dataset[key] ?? "") === dataMatch[2];
  }
  return false;
}

function datasetKey(attributeName) {
  return attributeName.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
}

class TestAudioElement extends TestElement {
  constructor() {
    super("audio");
    this.currentTime = 12;
    this.duration = Number.NaN;
    this.volume = 1;
    this.muted = false;
    this.paused = true;
    this.playCalls = 0;
    this.pauseCalls = 0;
    this.deferPlay = false;
    this.pendingPlayPromises = [];
  }

  async play() {
    this.playCalls += 1;
    this.paused = false;
    if (this.deferPlay) {
      return new Promise((resolve, reject) => {
        this.pendingPlayPromises.push({resolve, reject});
      });
    }
  }

  pause() {
    this.pauseCalls += 1;
    this.paused = true;
  }

  canPlayType() {
    return "probably";
  }

  load() {}

  resolvePlay(index = 0) {
    const pending = this.pendingPlayPromises[index];
    if (pending) {
      pending.resolve();
    }
  }

  rejectPlay(index = 0, error = Object.assign(
    new Error("The fetching process for the media resource was aborted by the user agent at the user's request."),
    {name: "AbortError"}
  )) {
    const pending = this.pendingPlayPromises[index];
    if (pending) {
      pending.reject(error);
    }
  }
}

class TestAudioBufferSource {
  constructor(context) {
    this.context = context;
    this.buffer = null;
    this.onended = () => {};
    this.startCalls = [];
    this.stopCalls = 0;
    this.connectedNode = null;
    this.disconnected = false;
    context.sources.push(this);
  }

  connect(node) {
    this.connectedNode = node;
  }

  disconnect() {
    this.disconnected = true;
  }

  start(when = 0, offset = 0, duration = undefined) {
    this.startCalls.push({when, offset, duration});
  }

  stop() {
    this.stopCalls += 1;
  }

  finish() {
    this.onended();
  }
}

class TestGainNode {
  constructor() {
    this.gain = {value: 1};
    this.connectedNode = null;
  }

  connect(node) {
    this.connectedNode = node;
  }
}

class TestAudioContext {
  static instances = [];

  constructor() {
    this.currentTime = 0;
    this.destination = {};
    this.state = "running";
    this.sources = [];
    this.decodedBuffers = [];
    TestAudioContext.instances.push(this);
  }

  createGain() {
    this.gainNode = new TestGainNode();
    return this.gainNode;
  }

  createBufferSource() {
    return new TestAudioBufferSource(this);
  }

  decodeAudioData(arrayBuffer, successCallback) {
    const duration = Number(arrayBuffer.duration) || 60;
    const sampleRate = Number(arrayBuffer.sampleRate) || 1000;
    const length = Math.max(1, Math.round(duration * sampleRate));
    const data = new Float32Array(length);
    data.fill(0.02);
    const silence = arrayBuffer.silence || {};
    const silentStartFrames = Math.min(length, Math.round((Number(silence.start) || 0) * sampleRate));
    const silentEndFrames = Math.min(length, Math.round((Number(silence.end) || 0) * sampleRate));
    data.fill(0, 0, silentStartFrames);
    data.fill(0, Math.max(0, length - silentEndFrames), length);
    const buffer = {
      duration,
      sampleRate,
      length,
      numberOfChannels: 1,
      url: arrayBuffer.url,
      getChannelData() {
        return data;
      },
    };
    this.decodedBuffers.push(buffer);
    if (successCallback) {
      successCallback(buffer);
      return undefined;
    }
    return Promise.resolve(buffer);
  }

  resume() {
    this.state = "running";
    return Promise.resolve();
  }
}

class TestDocument {
  constructor(elements) {
    this.elements = elements;
    this.body = new TestElement("body");
    this.body.dataset = {};
    this.activeElement = this.body;
    this.listeners = new Map();
    this.queryResults = new Map();
  }

  getElementById(id) {
    return this.elements[id] || null;
  }

  createElement(tagName) {
    return new TestElement(tagName);
  }

  createElementNS(_namespace, tagName) {
    return new TestElement(tagName);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  querySelector(selector) {
    const value = this.queryResults.get(selector);
    if (Array.isArray(value)) {
      return value[0] || null;
    }
    return value || null;
  }

  querySelectorAll(selector) {
    const value = this.queryResults.get(selector);
    if (Array.isArray(value)) {
      return value;
    }
    return value ? [value] : [];
  }

  setQueryResult(selector, value) {
    this.queryResults.set(selector, value);
  }
}

class TestFormData {
  constructor(form) {
    this.items = [];
    for (const control of form.elements || []) {
      if (!control.name || control.disabled) {
        continue;
      }
      if (
        control instanceof TestElement
        && (control.type === "checkbox" || control.type === "radio")
        && !control.checked
      ) {
        continue;
      }
      this.items.push([control.name, control.value]);
    }
  }

  delete(name) {
    this.items = this.items.filter(([key]) => key !== name);
  }

  *entries() {
    yield* this.items;
  }

  [Symbol.iterator]() {
    return this.entries();
  }
}

function createHarness(initialQueueState, options = {}) {
  if (options.gapless) {
    TestAudioContext.instances = [];
  }
  let objectUrlCounter = 0;
  const revokedObjectUrls = [];
  const deferredAudioBuffers = new Map();
  const deferredAudioBufferUrls = new Set(options.deferAudioBufferUrls || []);
  const rejectedAudioBufferUrls = new Set(options.rejectAudioBufferUrls || []);
  const animationCallbacks = new Map();
  let animationFrameId = 0;
  function audioBufferPayload(url) {
    const key = String(url);
    return {
      url: key,
      duration: trackDurationForUrl(key),
      sampleRate: 1000,
      silence: (options.audioSilenceByUrl && options.audioSilenceByUrl[key]) || {},
    };
  }
  function deferredAudioBuffer(url) {
    const key = String(url);
    let deferred = deferredAudioBuffers.get(key);
    if (!deferred) {
      let resolve;
      const promise = new Promise((resolver) => {
        resolve = resolver;
      });
      deferred = {promise, resolve};
      deferredAudioBuffers.set(key, deferred);
    }
    return deferred.promise.then(() => audioBufferPayload(key));
  }
  function trackDurationForUrl(url) {
    const absoluteUrl = new URL(url, "http://localhost/queue").toString();
    const snapshots = initialQueueState.track_snapshots || [];
    const track = snapshots.find((snapshot) => (
      new URL(snapshot.audioUrl, "http://localhost/queue").toString() === absoluteUrl
    ));
    return Number(track && track.durationSeconds) || 60;
  }
  class HarnessURL extends URL {
    static createObjectURL(blob) {
      objectUrlCounter += 1;
      return `blob:kukicha-${objectUrlCounter}-${blob.url || "audio"}`;
    }

    static revokeObjectURL(objectUrl) {
      revokedObjectUrls.push(objectUrl);
    }
  }
  const view = new TestElement("main");
  const meta = options.queueMeta ? new TestElement("div") : null;
  if (meta) {
    view.setQueryResult("[data-queue-meta]", meta);
  }

  const elements = {
    view,
    audio: new TestAudioElement(),
    play: new TestElement("button"),
    previous: new TestElement("button"),
    next: new TestElement("button"),
    "now-playing": new TestElement("div"),
    "playback-progress": new TestElement("input"),
    "elapsed-time": new TestElement("span"),
    "duration-time": new TestElement("span"),
    volume: new TestElement("input"),
    "volume-toggle": new TestElement("button"),
    "volume-icon": new TestElement("svg"),
    toast: new TestElement("div"),
    "job-toasts": new TestElement("div"),
    "confirmation-dialog": new TestElement("div"),
    "keyboard-shortcuts-dialog": new TestElement("dialog"),
    "queue-state": new TestElement("script"),
  };
  elements["confirmation-dialog"].hidden = true;
  elements["keyboard-shortcuts-dialog"].hidden = true;
  elements.audio.deferPlay = Boolean(options.deferAudioPlay);
  elements["queue-state"].textContent = JSON.stringify(initialQueueState);

  const document = new TestDocument(elements);
  document.body.dataset.page = options.page || "";
  const confirmationTitle = new TestElement("h2");
  const confirmationMessage = new TestElement("p");
  const confirmationCancel = new TestElement("button");
  const confirmationConfirm = new TestElement("button");
  confirmationCancel.closest = (selector) => (
    selector === "[data-confirmation-cancel]" ? confirmationCancel : null
  );
  confirmationConfirm.closest = (selector) => (
    selector === "[data-confirmation-confirm]" ? confirmationConfirm : null
  );
  document.setQueryResult("[data-confirmation-title]", confirmationTitle);
  document.setQueryResult("[data-confirmation-message]", confirmationMessage);
  document.setQueryResult("[data-confirmation-cancel]", confirmationCancel);
  document.setQueryResult("[data-confirmation-confirm]", confirmationConfirm);
  document.setQueryResult("[data-play-icon]", new TestElement("svg"));
  document.setQueryResult("[data-pause-icon]", new TestElement("svg"));

  const fetchCalls = [];
  const history = {
    state: {},
    scrollRestoration: "auto",
    replaceState(state) {
      this.state = state;
    },
    pushState(state) {
      this.state = state;
    },
  };
  const window = {
    document,
    history,
    location: {href: "http://localhost/queue", origin: "http://localhost"},
    navigator: {
      userAgent: options.userAgent || "Node.js",
      platform: options.platform || "",
      maxTouchPoints: options.maxTouchPoints || 0,
    },
    scrollX: 0,
    scrollY: 0,
    addEventListener() {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (callback) => {
      animationFrameId += 1;
      animationCallbacks.set(animationFrameId, callback);
      return animationFrameId;
    },
    cancelAnimationFrame: (id) => {
      animationCallbacks.delete(id);
    },
    scrollTo() {},
    scrollBy() {},
    URL: HarnessURL,
  };
  if (options.gapless) {
    window.AudioContext = TestAudioContext;
  }

  const context = {
    console,
    document,
    window,
    history,
    location: window.location,
    navigator: window.navigator,
    URL: HarnessURL,
    FormData: TestFormData,
    HTMLElement: TestElement,
    Element: TestElement,
    HTMLAnchorElement: TestElement,
    HTMLButtonElement: TestElement,
    HTMLDetailsElement: TestElement,
    HTMLFormElement: TestElement,
    HTMLImageElement: TestElement,
    HTMLInputElement: TestElement,
    HTMLSelectElement: TestElement,
    HTMLTextAreaElement: TestElement,
    HTMLTimeElement: TestElement,
    SVGSVGElement: TestElement,
    AudioContext: options.gapless ? TestAudioContext : undefined,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: window.requestAnimationFrame,
    cancelAnimationFrame: window.cancelAnimationFrame,
    fetch: async (url, request = {}) => {
      const parsedBody = request.body ? JSON.parse(request.body) : null;
      fetchCalls.push({url, request, body: parsedBody});
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            ...initialQueueState,
            ...(parsedBody || {}),
          };
        },
        async text() {
          return "";
        },
        async arrayBuffer() {
          const audioUrl = String(url);
          if (rejectedAudioBufferUrls.has(audioUrl)) {
            throw new Error("decode failed");
          }
          if (
            (options.deferAudioBuffers || deferredAudioBufferUrls.has(audioUrl))
            && audioUrl.startsWith("http://localhost/audio/")
          ) {
            return deferredAudioBuffer(url);
          }
          return audioBufferPayload(audioUrl);
        },
      };
    },
  };
  context.globalThis = context;
  context.self = context.window;

  vm.runInNewContext(playerScript, context, {filename: "player.js"});

  return {
    audio: elements.audio,
    context,
    document,
    fetchCalls,
    audioContexts: options.gapless ? TestAudioContext.instances : [],
    jobToasts: elements["job-toasts"],
    meta,
    nextButton: elements.next,
    playButton: elements.play,
    previousButton: elements.previous,
    revokedObjectUrls,
    resolveAudioBuffer(url) {
      const deferred = deferredAudioBuffers.get(String(url));
      if (deferred) {
        deferred.resolve();
      }
    },
    runAnimationFrame() {
      const callbacks = Array.from(animationCallbacks.entries());
      animationCallbacks.clear();
      for (const [, callback] of callbacks) {
        callback();
      }
    },
    view: elements.view,
    async flush() {
      await Promise.resolve();
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

function testInput(document, {type = "text", name = "", value = "", checked = false, disabled = false} = {}) {
  const input = document.createElement("input");
  input.type = type;
  input.name = name;
  input.value = value;
  input.checked = checked;
  input.disabled = disabled;
  return input;
}

function filterForm(document, controls = []) {
  const form = document.createElement("form");
  form.action = "/";
  form.dataset.defaultSort = "artist";
  form.dataset.filterForm = "";
  form.append(...controls);
  return form;
}

function searchControls(document, controls = []) {
  const element = document.createElement("div");
  element.className = "search-controls";
  element.append(...controls);
  return element;
}

function readonlyArtistFilter(document, artist) {
  const element = document.createElement("div");
  element.className = "readonly-filter artist-filter-readonly";
  element.dataset.readonlyArtistFilter = "";
  element.title = `Artist: ${artist}`;
  element.setAttribute("aria-label", `Artist filter: ${artist}`);
  const label = document.createElement("span");
  label.className = "readonly-filter-label";
  label.textContent = "Artist:";
  const value = document.createElement("span");
  value.className = "readonly-filter-value";
  value.textContent = artist;
  element.append(label, value);
  return element;
}

function sourceForUrl(context, url) {
  return context.sources.find((source) => source.buffer && source.buffer.url === url);
}

function latestSourceForUrl(context, url) {
  return [...context.sources].reverse().find((source) => source.buffer && source.buffer.url === url);
}

test("filter form submit helper closes search menu", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  const form = harness.document.createElement("form");
  const menu = harness.document.createElement("details");
  menu.open = true;
  form.setQueryResult("details[data-search-menu]", menu);

  harness.context.closeSearchMenu(form);

  assert.equal(menu.open, false);
});

test("browser-local dates are rendered from datetime attributes", () => {
  const previousTimezone = process.env.TZ;
  process.env.TZ = "America/New_York";
  try {
    const harness = createHarness({
      track_ids: [],
      position: 0,
      loaded_track_id: null,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
    });
    const added = harness.document.createElement("time");
    added.dataset.localDatePrefix = "Added";
    added.setAttribute("datetime", "2026-05-15T01:00:00+00:00");
    added.textContent = "Added 2026-05-15";
    const headingDate = harness.document.createElement("time");
    headingDate.dataset.localDate = "";
    headingDate.setAttribute("datetime", "2026-05-15T01:00:00+00:00");
    headingDate.textContent = "2026-05-15";
    harness.view.append(added, headingDate);

    harness.context.localizeBrowserTimes();

    assert.equal(added.textContent, "Added 2026-05-14");
    assert.equal(headingDate.textContent, "2026-05-14");
  } finally {
    if (previousTimezone === undefined) {
      delete process.env.TZ;
    } else {
      process.env.TZ = previousTimezone;
    }
  }
});

test("library filter form patch preserves artist hidden inputs before changing sort", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  const document = harness.document;
  const currentForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "size", value: ""}),
    testInput(document, {type: "radio", name: "sort", value: "recently_added"}),
    testInput(document, {type: "radio", name: "sort", value: "artist", checked: true}),
  ]);
  const nextForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "size", value: ""}),
    testInput(document, {type: "hidden", name: "artist", value: "Amon Tobin"}),
    testInput(document, {type: "radio", name: "sort", value: "recently_added"}),
    testInput(document, {type: "radio", name: "sort", value: "artist", checked: true}),
  ]);
  const currentPage = document.createElement("div");
  const nextPage = document.createElement("div");
  currentPage.setQueryResult("form[data-filter-form]", currentForm);
  nextPage.setQueryResult("form[data-filter-form]", nextForm);

  harness.context.syncLibraryFilterForm(currentPage, nextPage);
  const url = harness.context.formUrl(currentForm);

  assert.equal(url.searchParams.get("artist"), "Amon Tobin");
  assert.equal(url.searchParams.has("sort"), false);
});

test("library filter form patch syncs readonly artist filter label", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  const document = harness.document;
  const currentControls = searchControls(document);
  const currentForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "size", value: ""}),
    currentControls,
  ]);
  const nextForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "size", value: ""}),
    testInput(document, {type: "hidden", name: "artist", value: "Amon Tobin"}),
    searchControls(document, [readonlyArtistFilter(document, "Amon Tobin")]),
  ]);
  const currentPage = document.createElement("div");
  const nextPage = document.createElement("div");
  currentPage.setQueryResult("form[data-filter-form]", currentForm);
  nextPage.setQueryResult("form[data-filter-form]", nextForm);

  harness.context.syncLibraryFilterForm(currentPage, nextPage);

  let currentFilter = currentForm.querySelector("[data-readonly-artist-filter]");
  assert.ok(currentFilter);
  assert.equal(currentFilter.querySelector(".readonly-filter-value").textContent, "Amon Tobin");

  const clearedForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "size", value: ""}),
    searchControls(document),
  ]);
  nextPage.setQueryResult("form[data-filter-form]", clearedForm);

  harness.context.syncLibraryFilterForm(currentPage, nextPage);

  currentFilter = currentForm.querySelector("[data-readonly-artist-filter]");
  assert.equal(currentFilter, null);
});

test("album filter form urls add genre search and sort params to current params", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  const document = harness.document;

  const genreForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "artist", value: "Amon Tobin"}),
    testInput(document, {name: "search", value: "breaks"}),
    testInput(document, {type: "radio", name: "sort", value: "artist", checked: true}),
    testInput(document, {type: "hidden", name: "genre[0][p]", value: "Electronic"}),
  ]);
  const genreUrl = harness.context.formUrl(genreForm);
  assert.equal(genreUrl.searchParams.get("artist"), "Amon Tobin");
  assert.equal(genreUrl.searchParams.get("search"), "breaks");
  assert.equal(genreUrl.searchParams.has("sort"), false);
  assert.equal(genreUrl.searchParams.get("genre[0][p]"), "Electronic");

  const searchForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "artist", value: "Amon Tobin"}),
    testInput(document, {name: "search", value: "foley room"}),
    testInput(document, {type: "radio", name: "sort", value: "recently_added", checked: true}),
  ]);
  const searchUrl = harness.context.formUrl(searchForm);
  assert.equal(searchUrl.searchParams.get("artist"), "Amon Tobin");
  assert.equal(searchUrl.searchParams.get("search"), "foley room");
  assert.equal(searchUrl.searchParams.get("sort"), "recently_added");

  const sortForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "artist", value: "Amon Tobin"}),
    testInput(document, {name: "search", value: "out from"}),
    testInput(document, {type: "radio", name: "sort", value: "artist", checked: true}),
  ]);
  const sortUrl = harness.context.formUrl(sortForm);
  assert.equal(sortUrl.searchParams.get("artist"), "Amon Tobin");
  assert.equal(sortUrl.searchParams.get("search"), "out from");
  assert.equal(sortUrl.searchParams.has("sort"), false);
});

test("compact count formatting matches count label rules", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  assert.deepEqual(
    [
      999,
      1000,
      1200,
      12300,
      123000,
      1200000,
      12300000,
      12380000,
      123000000,
      999500,
      999000000000000,
      999000000000001,
    ].map((count) => harness.context.compactCount(count)),
    [
      "999",
      "1k",
      "1.2k",
      "12.3k",
      "123k",
      "1.2M",
      "12.3M",
      "12.4M",
      "123M",
      "1M",
      "999T",
      "infinity",
    ],
  );
});

test("cache clear uses non-blocking confirmation dialog", async () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  const button = new TestElement("button");
  const confirmButton = harness.document.querySelector("[data-confirmation-confirm]");
  const dialog = harness.document.getElementById("confirmation-dialog");
  const message = harness.document.querySelector("[data-confirmation-message]");
  button.dataset.clearUrl = "/api/cache/itunes-cover-artwork/clear";
  button.dataset.cacheLabel = "iTunes Cover Artwork";

  const clearPromise = harness.context.clearCache(button);
  await Promise.resolve();

  assert.equal(dialog.hidden, false);
  assert.equal(message.textContent, "Clear iTunes Cover Artwork cache?");
  assert.equal(harness.fetchCalls.length, 0);

  harness.document.listeners.get("click")[0]({
    target: confirmButton,
    preventDefault() {},
  });
  await clearPromise;
  await harness.flush();

  assert.equal(dialog.hidden, true);
  assert.equal(harness.fetchCalls[0].url, "/api/cache/itunes-cover-artwork/clear");
  assert.equal(harness.fetchCalls[0].request.method, "POST");
});

test("listening data reset uses non-blocking confirmation dialog", async () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  const button = new TestElement("button");
  const confirmButton = harness.document.querySelector("[data-confirmation-confirm]");
  const dialog = harness.document.getElementById("confirmation-dialog");
  const message = harness.document.querySelector("[data-confirmation-message]");
  button.dataset.resetUrl = "/api/listening-data/reset";

  const resetPromise = harness.context.resetListeningData(button);
  await Promise.resolve();

  assert.equal(dialog.hidden, false);
  assert.equal(message.textContent, "Reset listening data?");
  assert.equal(harness.fetchCalls.length, 0);

  harness.document.listeners.get("click")[0]({
    target: confirmButton,
    preventDefault() {},
  });
  await resetPromise;
  await harness.flush();

  assert.equal(dialog.hidden, true);
  assert.equal(harness.fetchCalls[0].url, "/api/listening-data/reset");
  assert.equal(harness.fetchCalls[0].request.method, "POST");
});

test("player control links ignore current album page params", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });
  harness.context.window.location.href = (
    "http://localhost/albums?artist=Existing+Artist&search=ambient&sort=artist"
  );

  assert.equal(
    harness.context.albumArtistFilterUrl("Amon Tobin"),
    "http://localhost/albums?artist=Amon+Tobin",
  );
  assert.equal(
    harness.context.albumDetailUrl({albumId: "amon-tobin::out-from-out-where"}),
    "http://localhost/albums/amon-tobin::out-from-out-where",
  );
});

test("combined album edit submit includes prefilled and cleared MusicBrainz URLs", async () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  const form = harness.document.createElement("form");
  form.action = "/api/albums/old-artist::album/edit";
  const topButton = harness.document.createElement("button");
  const bottomButton = harness.document.createElement("button");
  const status = harness.document.createElement("div");
  const albumInput = harness.document.createElement("input");
  const albumArtistInput = harness.document.createElement("input");
  const genreInput = harness.document.createElement("input");
  albumInput.value = "Manual Album";
  albumArtistInput.value = "Manual Artist";
  genreInput.value = "Manual Genre";

  const trackOneRow = harness.document.createElement("div");
  trackOneRow.dataset.trackId = "1";
  const trackOneArtist = harness.document.createElement("input");
  const trackOneNumber = harness.document.createElement("input");
  const trackOneTitle = harness.document.createElement("input");
  trackOneArtist.value = "Track Artist 1";
  trackOneNumber.value = "1";
  trackOneTitle.value = "Track Title 1";
  trackOneRow.setQueryResult("[data-track-artist-input]", trackOneArtist);
  trackOneRow.setQueryResult("[data-track-number-input]", trackOneNumber);
  trackOneRow.setQueryResult("[data-track-title-input]", trackOneTitle);

  const trackTwoRow = harness.document.createElement("div");
  trackTwoRow.dataset.trackId = "2";
  const trackTwoArtist = harness.document.createElement("input");
  const trackTwoNumber = harness.document.createElement("input");
  const trackTwoTitle = harness.document.createElement("input");
  trackTwoArtist.value = "Track Artist 2";
  trackTwoNumber.value = "2";
  trackTwoTitle.value = "Track Title 2";
  trackTwoRow.setQueryResult("[data-track-artist-input]", trackTwoArtist);
  trackTwoRow.setQueryResult("[data-track-number-input]", trackTwoNumber);
  trackTwoRow.setQueryResult("[data-track-title-input]", trackTwoTitle);

  function musicBrainzGroup(value, serverValue, trackId) {
    const group = harness.document.createElement("section");
    const urlInput = harness.document.createElement("input");
    urlInput.value = value;
    urlInput.setAttribute("data-server-value", serverValue);
    const trackIdInput = harness.document.createElement("input");
    trackIdInput.value = String(trackId);
    group.setQueryResult("[data-musicbrainz-url-input]", urlInput);
    group.setQueryResult("[data-musicbrainz-track-id]", [trackIdInput]);
    return {group, urlInput, trackIdInput};
  }

  const keptGroup = musicBrainzGroup(
    "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111",
    "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111",
    1
  );
  const clearedGroup = musicBrainzGroup(
    "",
    "https://musicbrainz.org/release/22222222-2222-2222-2222-222222222222",
    2
  );
  const unchangedBlankGroup = musicBrainzGroup("", "", 3);

  form.setQueryResult("[data-apply-album-edit]", [topButton, bottomButton]);
  form.setQueryResult("[data-album-edit-status]", status);
  form.setQueryResult("[data-album-input]", albumInput);
  form.setQueryResult("[data-album-artist-input]", albumArtistInput);
  form.setQueryResult("[data-album-genre-input]", genreInput);
  form.setQueryResult("[data-track-tag-row]", [trackOneRow, trackTwoRow]);
  form.setQueryResult(
    "[data-musicbrainz-group]",
    [keptGroup.group, clearedGroup.group, unchangedBlankGroup.group]
  );
  form.setQueryResult("input, textarea, select, button", [
    topButton,
    bottomButton,
    albumInput,
    albumArtistInput,
    genreInput,
    trackOneArtist,
    trackOneNumber,
    trackOneTitle,
    trackTwoArtist,
    trackTwoNumber,
    trackTwoTitle,
    keptGroup.urlInput,
    keptGroup.trackIdInput,
    clearedGroup.urlInput,
    clearedGroup.trackIdInput,
    unchangedBlankGroup.urlInput,
    unchangedBlankGroup.trackIdInput,
  ]);

  await harness.context.submitAlbumEditForm(form);
  await harness.flush();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/albums/old-artist::album/edit");
  assert.deepEqual(harness.fetchCalls[0].body, {
    tags: {
      album: "Manual Album",
      album_artist: "Manual Artist",
      genre: "Manual Genre",
      tracks: [
        {
          track_id: 1,
          artist: "Track Artist 1",
          track_number: "1",
          title: "Track Title 1",
        },
        {
          track_id: 2,
          artist: "Track Artist 2",
          track_number: "2",
          title: "Track Title 2",
        },
      ],
    },
    musicbrainz: {
      groups: [
        {
          musicbrainz_url: "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111",
          track_ids: [1],
        },
        {
          musicbrainz_url: "",
          track_ids: [2],
        },
      ],
    },
  });
  assert.equal(status.textContent, "Tag edit queued.");
  assert.equal(topButton.disabled, false);
  assert.equal(bottomButton.getAttribute("aria-busy"), null);
});

test("album edit submit can send MusicBrainz-only groups", async () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  const form = harness.document.createElement("form");
  form.action = "/api/albums/old-artist::album/edit";
  const topButton = harness.document.createElement("button");
  const bottomButton = harness.document.createElement("button");
  const status = harness.document.createElement("div");

  function musicBrainzGroup(value, serverValue, trackId) {
    const group = harness.document.createElement("section");
    const urlInput = harness.document.createElement("input");
    urlInput.value = value;
    urlInput.setAttribute("data-server-value", serverValue);
    const trackIdInput = harness.document.createElement("input");
    trackIdInput.value = String(trackId);
    group.setQueryResult("[data-musicbrainz-url-input]", urlInput);
    group.setQueryResult("[data-musicbrainz-track-id]", [trackIdInput]);
    return {group, urlInput, trackIdInput};
  }

  const setGroup = musicBrainzGroup(
    "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111",
    "",
    1
  );
  const clearedGroup = musicBrainzGroup(
    "",
    "https://musicbrainz.org/release/22222222-2222-2222-2222-222222222222",
    2
  );

  form.setQueryResult("[data-apply-album-edit]", [topButton, bottomButton]);
  form.setQueryResult("[data-album-edit-status]", status);
  form.setQueryResult("[data-musicbrainz-group]", [setGroup.group, clearedGroup.group]);
  form.setQueryResult("input, textarea, select, button", [
    topButton,
    bottomButton,
    setGroup.urlInput,
    setGroup.trackIdInput,
    clearedGroup.urlInput,
    clearedGroup.trackIdInput,
  ]);

  await harness.context.submitAlbumEditForm(form);
  await harness.flush();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/albums/old-artist::album/edit");
  assert.deepEqual(harness.fetchCalls[0].body, {
    musicbrainz: {
      groups: [
        {
          musicbrainz_url: "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111",
          track_ids: [1],
        },
        {
          musicbrainz_url: "",
          track_ids: [2],
        },
      ],
    },
  });
  assert.equal(status.textContent, "Tag edit queued.");
  assert.equal(topButton.disabled, false);
  assert.equal(bottomButton.getAttribute("aria-busy"), null);
});

test("album edit MusicBrainz URL disables only album-level tag fields", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  const form = harness.document.createElement("form");
  form.setAttribute("data-album-edit-form", "");
  const group = harness.document.createElement("section");
  const urlInput = harness.document.createElement("input");
  const albumInput = harness.document.createElement("input");
  const albumArtistInput = harness.document.createElement("input");
  const genreInput = harness.document.createElement("input");
  const trackArtistInput = harness.document.createElement("input");
  const trackNumberInput = harness.document.createElement("input");
  const trackTitleInput = harness.document.createElement("input");
  const note = harness.document.createElement("p");
  note.hidden = true;

  form.setQueryResult("[data-musicbrainz-group]", [group]);
  form.setQueryResult("[data-musicbrainz-url-input]", urlInput);
  form.setQueryResult("[data-album-input]", albumInput);
  form.setQueryResult("[data-album-artist-input]", albumArtistInput);
  form.setQueryResult("[data-album-genre-input]", genreInput);
  form.setQueryResult("[data-album-level-musicbrainz-note]", note);

  urlInput.value = "https://musicbrainz.org/release/11111111-1111-1111-1111-111111111111";
  harness.context.syncAlbumEditAlbumLevelFields(form);

  assert.equal(albumInput.disabled, true);
  assert.equal(albumArtistInput.disabled, true);
  assert.equal(genreInput.disabled, true);
  assert.equal(trackArtistInput.disabled, false);
  assert.equal(trackNumberInput.disabled, false);
  assert.equal(trackTitleInput.disabled, false);
  assert.equal(note.hidden, false);

  urlInput.value = "";
  harness.context.syncAlbumEditAlbumLevelFields(form);

  assert.equal(albumInput.disabled, false);
  assert.equal(albumArtistInput.disabled, false);
  assert.equal(genreInput.disabled, false);
  assert.equal(trackArtistInput.disabled, false);
  assert.equal(trackNumberInput.disabled, false);
  assert.equal(trackTitleInput.disabled, false);
  assert.equal(note.hidden, true);
});

test("play restarts from the first playable track after a non-empty queue is exhausted", async () => {
  const harness = createHarness({
    track_ids: [1, 2],
    position: 2,
    loaded_track_id: 2,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  harness.playButton.click();
  await harness.flush();

  assert.equal(harness.audio.src, "/audio/1");
  assert.equal(harness.audio.currentTime, 0);
  assert.equal(harness.audio.playCalls, 1);
  const playbackCall = harness.fetchCalls.find((call) => call.url === "/api/playback");
  assert.ok(playbackCall);
  assert.deepEqual(playbackCall.body, {
    loaded_track_id: 1,
    position: 0,
    paused: false,
    errored_track_ids: [],
  });
  const scrobbleCall = harness.fetchCalls.find((call) => call.url === "/api/scrobble");
  assert.ok(scrobbleCall);
  assert.deepEqual(
    {
      playback_id: scrobbleCall.body.playback_id,
      submission: scrobbleCall.body.submission,
    },
    {playback_id: 1, submission: false},
  );
});

test("play starts now playing and natural end submits scrobble", async () => {
  const harness = createHarness({
    track_ids: [7],
    position: 0,
    loaded_track_id: 7,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  harness.playButton.click();
  await harness.flush();

  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.equal(harness.fetchCalls[1].url, "/api/scrobble");
  assert.deepEqual(
    {
      playback_id: harness.fetchCalls[1].body.playback_id,
      submission: harness.fetchCalls[1].body.submission,
    },
    {playback_id: 7, submission: false},
  );

  harness.fetchCalls.splice(0);
  harness.audio.listeners.get("ended")[0]();
  await harness.flush();

  assert.equal(harness.fetchCalls[0].url, "/api/scrobble");
  assert.deepEqual(
    {
      playback_id: harness.fetchCalls[0].body.playback_id,
      submission: harness.fetchCalls[0].body.submission,
    },
    {playback_id: 7, submission: true},
  );
  assert.equal(typeof harness.fetchCalls[0].body.time, "number");
  assert.equal(harness.fetchCalls.at(-1).url, "/api/playback");
  assert.deepEqual(harness.fetchCalls.at(-1).body, {
    position: 1,
    paused: true,
    errored_track_ids: [],
  });
});

test("submitted tracked playlist item scrobble refreshes home stats", async () => {
  const harness = createHarness(
    {
      track_ids: [-7],
      position: 0,
      loaded_track_id: -7,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {
          trackId: -7,
          audioUrl: "/playlist-audio/7",
          title: "Playlist Track",
          albumArtist: "Harold Budd And Brian Eno",
          album: "The Pearl",
          durationSeconds: 180,
        },
      ],
    },
    {page: "home"},
  );
  harness.context.window.location.href = "http://localhost/";

  harness.playButton.click();
  await harness.flush();

  harness.fetchCalls.splice(0);
  harness.audio.listeners.get("ended")[0]();
  await harness.flush();
  await harness.flush();

  assert.deepEqual(
    harness.fetchCalls
      .filter((call) => call.url === "/api/scrobble")
      .map((call) => ({
        playback_id: call.body.playback_id,
        submission: call.body.submission,
      })),
    [
      {playback_id: -7, submission: true},
    ],
  );
  assert.ok(
    harness.fetchCalls.some((call) => (
      call.url === "http://localhost/"
      && call.request.headers["X-Kukicha-Fragment"] === "1"
    )),
  );
});

test("indeterminate stream submits played scrobble on play", async () => {
  const harness = createHarness({
    track_ids: [-7],
    position: 0,
    loaded_track_id: -7,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
    track_snapshots: [
      {
        trackId: -7,
        audioUrl: "https://example.test/live",
        title: "Live Stream",
        durationIsIndeterminate: true,
      },
    ],
  });

  harness.playButton.click();
  await harness.flush();
  await harness.flush();

  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.deepEqual(
    harness.fetchCalls
      .filter((call) => call.url === "/api/scrobble")
      .map((call) => ({
        playback_id: call.body.playback_id,
        submission: call.body.submission,
      })),
    [
      {playback_id: -7, submission: true},
    ],
  );

  harness.fetchCalls.splice(0);
  harness.playButton.click();
  harness.playButton.click();
  await harness.flush();
  await harness.flush();

  assert.equal(harness.audio.playCalls, 2);
  assert.deepEqual(
    harness.fetchCalls
      .filter((call) => call.url === "/api/scrobble" && call.body.submission)
      .map((call) => call.body.playback_id),
    [],
  );

  harness.fetchCalls.splice(0);
  harness.audio.listeners.get("ended")[0]();
  await harness.flush();

  assert.deepEqual(
    harness.fetchCalls
      .filter((call) => call.url === "/api/scrobble" && call.body.submission)
      .map((call) => call.body.playback_id),
    [],
  );
});

test("now playing scrobble refreshes the home continue section", async () => {
  const harness = createHarness(
    {
      track_ids: [7],
      position: 0,
      loaded_track_id: 7,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
    },
    {page: "home"},
  );
  harness.context.window.location.href = "http://localhost/";

  harness.playButton.click();
  await harness.flush();

  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.equal(harness.fetchCalls[1].url, "/api/scrobble");
  assert.equal(harness.fetchCalls[2].url, "http://localhost/");
  assert.equal(harness.fetchCalls[2].request.headers["X-Kukicha-Fragment"], "1");
});

test("continue listening cover button toggles existing queue playback", async () => {
  const harness = createHarness(
    {
      track_ids: [7],
      position: 0,
      loaded_track_id: 7,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
    },
    {page: "home"},
  );
  const button = new TestElement("button");
  const continuePlayIcon = new TestElement("span");
  const continuePauseIcon = new TestElement("span");
  harness.document.setQueryResult("[data-continue-play-toggle]", button);
  harness.document.setQueryResult("[data-play-icon]", [new TestElement("span"), continuePlayIcon]);
  harness.document.setQueryResult("[data-pause-icon]", [new TestElement("span"), continuePauseIcon]);
  button.closest = (selector) => (
    selector === "[data-continue-play-toggle]" ? button : null
  );
  const event = {
    target: button,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };

  harness.document.listeners.get("click")[0](event);
  await harness.flush();

  assert.equal(event.defaultPrevented, true);
  assert.equal(harness.audio.playCalls, 1);
  assert.equal(button.getAttribute("aria-label"), "Pause current queue");
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.title, "Pause");
  assert.equal(continuePlayIcon.hidden, true);
  assert.equal(continuePauseIcon.hidden, false);
  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.deepEqual(harness.fetchCalls[0].body, {
    loaded_track_id: 7,
    position: 0,
    paused: false,
    errored_track_ids: [],
  });
  assert.equal(harness.fetchCalls[1].url, "/api/scrobble");
  assert.deepEqual(
    {
      playback_id: harness.fetchCalls[1].body.playback_id,
      submission: harness.fetchCalls[1].body.submission,
    },
    {playback_id: 7, submission: false},
  );
});

test("queue page played count follows next and previous queue selection", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2, 3],
      position: 1,
      loaded_track_id: 2,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
    },
    {page: "queue", queueMeta: true}
  );

  harness.nextButton.click();
  await harness.flush();

  assert.equal(harness.meta.textContent, "3 tracks - 2 played");
  assert.equal(harness.audio.src, "/audio/3");

  harness.previousButton.click();
  await harness.flush();

  assert.equal(harness.meta.textContent, "3 tracks - 1 played");
  assert.equal(harness.audio.src, "/audio/2");
});

test("web audio gapless starts finite same-origin tracks without changing scrobble payloads", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
      ],
    },
    {gapless: true},
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();

  assert.equal(harness.audio.playCalls, 1);
  assert.equal(harness.audioContexts.length, 1);
  const context = harness.audioContexts[0];
  const currentSource = sourceForUrl(context, "http://localhost/audio/1");
  const nextSource = latestSourceForUrl(context, "http://localhost/audio/2");
  assert.ok(currentSource);
  assert.ok(nextSource);
  assert.deepEqual(currentSource.startCalls, [{when: 0, offset: 0, duration: 90}]);
  assert.deepEqual(nextSource.startCalls, [{when: 90, offset: 0, duration: 120}]);
  assert.ok(harness.fetchCalls.some((call) => call.url === "http://localhost/audio/1"));
  assert.ok(harness.fetchCalls.some((call) => call.url === "http://localhost/audio/2"));
  const playbackCall = harness.fetchCalls.find((call) => call.url === "/api/playback");
  assert.ok(playbackCall);
  assert.deepEqual(playbackCall.body, {
    loaded_track_id: 1,
    position: 0,
    paused: false,
    errored_track_ids: [],
  });
  const scrobbleCall = harness.fetchCalls.find((call) => call.url === "/api/scrobble");
  assert.ok(scrobbleCall);
  assert.deepEqual(
    {
      playback_id: scrobbleCall.body.playback_id,
      submission: scrobbleCall.body.submission,
    },
    {playback_id: 1, submission: false},
  );
});

test("web audio starts html playback while the current buffer decodes", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
      ],
    },
    {gapless: true, deferAudioBufferUrls: ["http://localhost/audio/1"]},
  );

  harness.playButton.click();
  await harness.flush();
  const context = harness.audioContexts[0];

  assert.equal(harness.audio.playCalls, 1);
  assert.equal(harness.audio.src, "/audio/1");
  assert.deepEqual(context.sources, []);
  assert.ok(harness.fetchCalls.some((call) => call.url === "http://localhost/audio/1"));
  assert.ok(harness.fetchCalls.some((call) => call.url === "http://localhost/audio/2"));

  harness.resolveAudioBuffer("http://localhost/audio/1");
  await harness.flush();
  await harness.flush();

  const currentSource = sourceForUrl(context, "http://localhost/audio/1");
  const nextSource = latestSourceForUrl(context, "http://localhost/audio/2");
  assert.ok(currentSource);
  assert.ok(nextSource);
  assert.deepEqual(currentSource.startCalls, [{when: 0, offset: 0, duration: 90}]);
  assert.deepEqual(nextSource.startCalls, [{when: 90, offset: 0, duration: 120}]);
});

test("web audio ignores stale html media aborts after switching queue tracks", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
      ],
    },
    {gapless: true, deferAudioPlay: true, deferAudioBufferUrls: ["http://localhost/audio/1"]},
  );

  harness.playButton.click();
  await harness.flush();
  assert.equal(harness.audio.playCalls, 1);
  assert.equal(harness.audio.src, "/audio/1");

  harness.nextButton.click();
  await harness.flush();
  await harness.flush();
  assert.equal(harness.audio.playCalls, 2);
  assert.ok(harness.fetchCalls.some((call) => (
    call.url === "/api/playback"
    && call.body.loaded_track_id === 2
  )));

  harness.audio.rejectPlay(0);
  await harness.flush();

  assert.ok(!harness.fetchCalls.some((call) => (
    call.url === "/api/playback"
    && Array.isArray(call.body.errored_track_ids)
    && call.body.errored_track_ids.includes(1)
  )));
});

test("web audio trims tiny decoded silence at buffer boundaries", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 10},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 12},
      ],
    },
    {
      gapless: true,
      audioSilenceByUrl: {
        "http://localhost/audio/1": {end: 0.04},
        "http://localhost/audio/2": {start: 0.03},
      },
    },
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();

  const context = harness.audioContexts[0];
  assert.deepEqual(
    sourceForUrl(context, "http://localhost/audio/1").startCalls,
    [{when: 0, offset: 0, duration: 9.96}],
  );
  assert.deepEqual(
    latestSourceForUrl(context, "http://localhost/audio/2").startCalls,
    [{when: 9.96, offset: 0.03, duration: 11.97}],
  );
});

test("web audio natural transitions submit finished and next-track scrobbles in order", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
      ],
    },
    {gapless: true},
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();
  const context = harness.audioContexts[0];
  harness.fetchCalls.splice(0);

  sourceForUrl(context, "http://localhost/audio/1").finish();
  await harness.flush();
  await harness.flush();

  assert.ok(!harness.fetchCalls.some((call) => call.url === "http://localhost/audio/2"));
  assert.deepEqual(
    harness.fetchCalls
      .filter((call) => call.url === "/api/scrobble" || call.url === "/api/playback")
      .map((call) => call.url === "/api/scrobble"
        ? {
            url: call.url,
            playback_id: call.body.playback_id,
            submission: call.body.submission,
          }
        : {
            url: call.url,
            loaded_track_id: call.body.loaded_track_id,
            position: call.body.position,
            paused: call.body.paused,
          }),
    [
      {url: "/api/scrobble", playback_id: 1, submission: true},
      {url: "/api/playback", loaded_track_id: 2, position: 1, paused: false},
      {url: "/api/scrobble", playback_id: 2, submission: false},
    ],
  );
});

test("web audio waits for in-flight next-track prefetch at transition", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
      ],
    },
    {gapless: true, deferAudioBufferUrls: ["http://localhost/audio/2"]},
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();
  const context = harness.audioContexts[0];
  assert.deepEqual(
    context.sources.map((source) => source.buffer && source.buffer.url),
    ["http://localhost/audio/1"],
  );
  assert.ok(harness.fetchCalls.some((call) => call.url === "http://localhost/audio/2"));
  harness.fetchCalls.splice(0);

  context.sources[0].finish();
  await harness.flush();
  assert.deepEqual(
    context.sources.map((source) => source.buffer && source.buffer.url),
    ["http://localhost/audio/1"],
  );

  harness.resolveAudioBuffer("http://localhost/audio/2");
  await harness.flush();
  await harness.flush();

  assert.deepEqual(
    context.sources.map((source) => source.buffer && source.buffer.url),
    ["http://localhost/audio/1", "http://localhost/audio/2"],
  );
  assert.ok(!harness.fetchCalls.some((call) => call.url === "http://localhost/audio/2"));
  assert.deepEqual(
    harness.fetchCalls
      .filter((call) => call.url === "/api/playback" || call.url === "/api/scrobble")
      .map((call) => call.url === "/api/playback"
        ? {
            url: call.url,
            loaded_track_id: call.body.loaded_track_id,
            position: call.body.position,
            paused: call.body.paused,
          }
        : {
            url: call.url,
            playback_id: call.body.playback_id,
            submission: call.body.submission,
          }),
    [
      {url: "/api/scrobble", playback_id: 1, submission: true},
      {url: "/api/playback", loaded_track_id: 2, position: 1, paused: false},
      {url: "/api/scrobble", playback_id: 2, submission: false},
    ],
  );
});

test("web audio final track finish exhausts the queue after submitting played scrobble", async () => {
  const harness = createHarness(
    {
      track_ids: [7],
      position: 0,
      loaded_track_id: 7,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 7, audioUrl: "/audio/7", title: "Seven", durationSeconds: 180},
      ],
    },
    {gapless: true},
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();
  harness.fetchCalls.splice(0);

  harness.audioContexts[0].sources[0].finish();
  await harness.flush();

  assert.equal(harness.fetchCalls[0].url, "/api/scrobble");
  assert.deepEqual(
    {
      playback_id: harness.fetchCalls[0].body.playback_id,
      submission: harness.fetchCalls[0].body.submission,
    },
    {playback_id: 7, submission: true},
  );
  assert.equal(harness.fetchCalls.at(-1).url, "/api/playback");
  assert.deepEqual(harness.fetchCalls.at(-1).body, {
    position: 1,
    paused: true,
    errored_track_ids: [],
  });
});

test("web audio pause resume and seeking stay behind the active engine", async () => {
  const harness = createHarness(
    {
      track_ids: [4, 5],
      position: 0,
      loaded_track_id: 4,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 4, audioUrl: "/audio/4", title: "Four", durationSeconds: 100},
        {trackId: 5, audioUrl: "/audio/5", title: "Five", durationSeconds: 90},
      ],
    },
    {gapless: true},
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();
  const context = harness.audioContexts[0];
  assert.ok(harness.fetchCalls.some((call) => call.url === "http://localhost/audio/5"));
  assert.ok(sourceForUrl(context, "http://localhost/audio/4"));
  assert.ok(latestSourceForUrl(context, "http://localhost/audio/5"));
  harness.fetchCalls.splice(0);

  harness.playButton.click();
  await harness.flush();

  assert.equal(sourceForUrl(context, "http://localhost/audio/4").stopCalls, 1);
  assert.equal(latestSourceForUrl(context, "http://localhost/audio/5").stopCalls, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.deepEqual(harness.fetchCalls[0].body, {
    paused: true,
    loaded_track_id: 4,
  });

  harness.fetchCalls.splice(0);
  harness.playButton.click();
  await harness.flush();
  await harness.flush();
  assert.equal(context.sources.at(-2).buffer.url, "http://localhost/audio/4");
  assert.equal(context.sources.at(-1).buffer.url, "http://localhost/audio/5");
  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.equal(harness.fetchCalls[1].url, "/api/scrobble");

  harness.document.elements["playback-progress"].value = "500";
  harness.document.elements["playback-progress"].listeners.get("input")[0]();
  const seekedSource = latestSourceForUrl(context, "http://localhost/audio/4");
  assert.deepEqual(seekedSource.startCalls, [{when: 0, offset: 50, duration: 50}]);
  assert.equal(latestSourceForUrl(context, "http://localhost/audio/5").buffer.url, "http://localhost/audio/5");
  assert.equal(harness.document.elements["elapsed-time"].textContent, "0:50");
  assert.equal(harness.document.elements["playback-progress"].value, "500");
});

test("web audio next and previous rebuild from the selected queue position", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2, 3],
      position: 1,
      loaded_track_id: 2,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
        {trackId: 3, audioUrl: "/audio/3", title: "Three", durationSeconds: 150},
      ],
    },
    {page: "queue", queueMeta: true, gapless: true},
  );

  harness.nextButton.click();
  await harness.flush();
  await harness.flush();
  const context = harness.audioContexts[0];

  assert.equal(harness.meta.textContent, "3 tracks - 2 played - 6 minutes");
  assert.equal(context.sources.at(-1).buffer.url, "http://localhost/audio/3");

  harness.previousButton.click();
  await harness.flush();
  await harness.flush();

  assert.equal(harness.meta.textContent, "3 tracks - 1 played - 6 minutes");
  assert.equal(context.sources.at(-2).buffer.url, "http://localhost/audio/2");
  assert.equal(context.sources.at(-1).buffer.url, "http://localhost/audio/3");
});

test("web audio prefetch errors mark the failed next track", async () => {
  const harness = createHarness(
    {
      track_ids: [1, 2],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
        {trackId: 2, audioUrl: "/audio/2", title: "Two", durationSeconds: 120},
      ],
    },
    {gapless: true, rejectAudioBufferUrls: ["http://localhost/audio/2"]},
  );

  harness.playButton.click();
  await harness.flush();
  await harness.flush();

  assert.equal(harness.audioContexts.length, 1);
  assert.equal(sourceForUrl(harness.audioContexts[0], "http://localhost/audio/1").buffer.url, "http://localhost/audio/1");
  const playbackCall = harness.fetchCalls.find((call) => (
    call.url === "/api/playback"
    && Array.isArray(call.body.errored_track_ids)
    && call.body.errored_track_ids.includes(2)
  ));
  assert.ok(playbackCall);
  assert.deepEqual(playbackCall.body, {
    loaded_track_id: 1,
    paused: false,
    errored_track_ids: [2],
  });
});

test("ios safari and non-gapless tracks keep the native audio engine", async () => {
  const iosHarness = createHarness(
    {
      track_ids: [1],
      position: 0,
      loaded_track_id: 1,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {trackId: 1, audioUrl: "/audio/1", title: "One", durationSeconds: 90},
      ],
    },
    {
      gapless: true,
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    },
  );

  iosHarness.playButton.click();
  await iosHarness.flush();

  assert.equal(iosHarness.audioContexts.length, 0);
  assert.equal(iosHarness.audio.playCalls, 1);
  assert.equal(iosHarness.audio.src, "/audio/1");

  const streamHarness = createHarness(
    {
      track_ids: [-7],
      position: 0,
      loaded_track_id: -7,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {
          trackId: -7,
          audioUrl: "/playlist-audio/7",
          title: "Live Stream",
          durationIsIndeterminate: true,
        },
      ],
    },
    {gapless: true},
  );

  streamHarness.playButton.click();
  await streamHarness.flush();

  assert.equal(streamHarness.audioContexts.length, 0);
  assert.equal(streamHarness.audio.playCalls, 1);
  assert.equal(streamHarness.audio.src, "/playlist-audio/7");

  const remoteHarness = createHarness(
    {
      track_ids: [-8],
      position: 0,
      loaded_track_id: -8,
      paused: true,
      errored_track_ids: [],
      unavailable_track_ids: [],
      track_snapshots: [
        {
          trackId: -8,
          audioUrl: "https://example.test/track.mp3",
          title: "Remote Track",
          durationSeconds: 200,
        },
      ],
    },
    {gapless: true},
  );

  remoteHarness.playButton.click();
  await remoteHarness.flush();

  assert.equal(remoteHarness.audioContexts.length, 0);
  assert.equal(remoteHarness.audio.playCalls, 1);
  assert.equal(remoteHarness.audio.src, "https://example.test/track.mp3");
});

test("job toast does not rewind from running to queued", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  harness.context.showJobToast({
    job_id: 60,
    kind: "rescan_library",
    kind_label: "Rescan",
    status: "running",
    status_label: "Running",
    message: "Rescan running.",
    updated_at: "2026-05-05T11:24:46Z",
  });
  harness.context.showJobToast({
    job_id: 60,
    kind: "rescan_library",
    kind_label: "Rescan",
    status: "queued",
    status_label: "Queued",
    message: "Rescan queued.",
    updated_at: "2026-05-05T11:24:45Z",
  });

  const toast = harness.jobToasts.children[0];
  assert.equal(harness.jobToasts.children.length, 1);
  assert.equal(toast.className, "job-toast running");
  assert.equal(toast.querySelector(".job-toast-message").textContent, "Rescan running.");
});

test("terminal job toast does not rewind to running", () => {
  const harness = createHarness({
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: [],
  });

  harness.context.showJobToast({
    job_id: 61,
    kind: "edit_album",
    kind_label: "Tag edit",
    status: "succeeded",
    status_label: "Succeeded",
    message: "Tags saved.",
    updated_at: "2026-05-05T11:27:26Z",
  });
  harness.context.showJobToast({
    job_id: 61,
    kind: "edit_album",
    kind_label: "Tag edit",
    status: "running",
    status_label: "Running",
    message: "Tag edit running.",
    updated_at: "2026-05-05T11:27:25Z",
  });

  const toast = harness.jobToasts.children[0];
  assert.equal(harness.jobToasts.children.length, 1);
  assert.equal(toast.className, "job-toast succeeded");
  assert.equal(toast.querySelector(".job-toast-message").textContent, "Tags saved.");
});
