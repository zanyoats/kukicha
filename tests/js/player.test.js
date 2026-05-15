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
    context,
    document,
    fetchCalls,
    jobToasts: elements["job-toasts"],
    meta,
    nextButton: elements.next,
    playButton: elements.play,
    previousButton: elements.previous,
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
    testInput(document, {type: "hidden", name: "per_page", value: ""}),
    testInput(document, {type: "radio", name: "sort", value: "recently_added"}),
    testInput(document, {type: "radio", name: "sort", value: "artist", checked: true}),
  ]);
  const nextForm = filterForm(document, [
    testInput(document, {type: "hidden", name: "per_page", value: ""}),
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
  assert.equal(harness.fetchCalls[0].url, "/api/playback");
  assert.deepEqual(harness.fetchCalls[0].body, {
    loaded_track_id: 1,
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
