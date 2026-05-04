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
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
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
    return value || null;
  }

  querySelectorAll(selector) {
    const value = this.queryResults.get(selector);
    if (Array.isArray(value)) {
      return value;
    }
    return value ? [value] : [];
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

  remove() {}

  after() {}

  focus() {}

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
  }

  async play() {
    this.playCalls += 1;
    this.paused = false;
  }

  pause() {
    this.pauseCalls += 1;
    this.paused = true;
  }

  load() {}
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

function createHarness(initialQueueState, options = {}) {
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
    "keyboard-shortcuts-dialog": new TestElement("dialog"),
    "queue-state": new TestElement("script"),
  };
  elements["queue-state"].textContent = JSON.stringify(initialQueueState);

  const document = new TestDocument(elements);
  document.body.dataset.page = options.page || "";
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
    navigator: {userAgent: "Node.js"},
    scrollX: 0,
    scrollY: 0,
    addEventListener() {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (callback) => setTimeout(callback, 0),
    cancelAnimationFrame: clearTimeout,
    scrollTo() {},
    scrollBy() {},
  };

  const context = {
    console,
    document,
    window,
    history,
    location: window.location,
    navigator: window.navigator,
    URL,
    FormData,
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
      };
    },
  };
  context.globalThis = context;
  context.self = context.window;

  vm.runInNewContext(playerScript, context, {filename: "player.js"});

  return {
    audio: elements.audio,
    document,
    fetchCalls,
    meta,
    nextButton: elements.next,
    playButton: elements.play,
    previousButton: elements.previous,
    async flush() {
      await Promise.resolve();
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

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
  assert.deepEqual(harness.fetchCalls.at(-1).body, {
    loaded_track_id: 1,
    position: 0,
    paused: false,
    errored_track_ids: [],
  });
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
