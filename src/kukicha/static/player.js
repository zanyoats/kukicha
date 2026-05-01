const fragmentHeader = "X-Kukicha-Fragment";
const view = document.getElementById("view");
const audio = document.getElementById("audio");
const playButton = document.getElementById("play");
const previousButton = document.getElementById("previous");
const nextButton = document.getElementById("next");
const queueLink = document.getElementById("queue-link");
const nowPlaying = document.getElementById("now-playing");
const toast = document.getElementById("toast");
const jobToasts = document.getElementById("job-toasts");
const trackCache = new Map();
const albumPlaybackCache = new Map();
const dropdownMenuSelector = "details[data-dropdown-menu]";
const toastHideDelayMs = readToastDelayMs("toastTimeoutMs", 10000);
const linkedToastHideDelayMs = readToastDelayMs("linkedToastTimeoutMs", 25000);

let queueState = readInitialQueueState();
let appHistoryDepth = initialAppHistoryDepth();
let scrollSaveFrame = 0;
let isRestoringScroll = false;
let scrollRestoreToken = 0;
const toastTimeouts = new WeakMap();
let jobsSource = null;
let jobsStreamLoadPending = false;
let suppressPauseStateUntilPlay = false;
let pendingPauseCommitTimeout = 0;
let manualPauseRequested = false;
let activePlaylistMenu = null;
let activePlaylistOptions = null;
let activePlaylistSourceOptions = null;

function readToastDelayMs(datasetKey, fallback) {
  if (!(toast instanceof HTMLElement)) {
    return fallback;
  }
  const value = Number(toast.dataset[datasetKey]);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

initializeHistoryState();
focusInitialSearch();
syncFilterSummaries();
syncRootForms();
syncAlbumArtistMappingForms();
localizeJobTimes();
syncJobsStream();

function initialAppHistoryDepth() {
  const depth = Number(history.state && history.state.kukichaDepth);
  return Number.isFinite(depth) && depth > 0 ? depth : 0;
}

function initializeHistoryState() {
  const currentState = history.state && typeof history.state === "object" ? history.state : {};
  const scrollX = Number(currentState.kukichaScrollX);
  const scrollY = Number(currentState.kukichaScrollY);
  try {
    history.replaceState({
      ...currentState,
      kukichaDepth: appHistoryDepth,
      kukichaScrollX: Number.isFinite(scrollX) ? scrollX : window.scrollX,
      kukichaScrollY: Number.isFinite(scrollY) ? scrollY : window.scrollY
    }, "", window.location.href);
    if ("scrollRestoration" in history) {
      history.scrollRestoration = "manual";
    }
  } catch {
    return;
  }
}

function readInitialQueueState() {
  const source = document.getElementById("queue-state");
  if (!source) {
    return {track_ids: [], position: 0, loaded_track_id: null, paused: true, errored_track_ids: []};
  }
  try {
    return normalizeQueueState(JSON.parse(source.textContent));
  } catch {
    return {track_ids: [], position: 0, loaded_track_id: null, paused: true, errored_track_ids: []};
  }
}

function normalizeQueueState(state) {
  const trackIds = state && Array.isArray(state.track_ids)
    ? state.track_ids.map(Number).filter(Number.isFinite)
    : [];
  if (!trackIds.length) {
    return {track_ids: [], position: 0, loaded_track_id: null, paused: true, errored_track_ids: []};
  }
  const validTrackIds = new Set(trackIds);
  const erroredTrackIds = state && Array.isArray(state.errored_track_ids)
    ? Array.from(new Set(
        state.errored_track_ids
          .map(Number)
          .filter((trackId) => Number.isFinite(trackId) && validTrackIds.has(trackId))
      ))
    : [];
  const loadedTrackIdValue = state && state.loaded_track_id === null
    ? null
    : Number(state && state.loaded_track_id);
  let position = Number(state && state.position);
  position = Number.isFinite(position) ? Math.trunc(position) : 0;
  position = Math.max(0, Math.min(position, trackIds.length));
  let loadedTrackId = Number.isFinite(loadedTrackIdValue) ? loadedTrackIdValue : null;
  if (loadedTrackId !== null && !trackIds.includes(loadedTrackId)) {
    loadedTrackId = null;
  }
  if (loadedTrackId === null && position < trackIds.length) {
    loadedTrackId = trackIds[position];
  }
  return {
    track_ids: trackIds,
    position,
    loaded_track_id: loadedTrackId,
    paused: loadedTrackId === null ? true : !state || state.paused !== false,
    errored_track_ids: erroredTrackIds
  };
}

function focusInitialSearch() {
  const target = view.querySelector("[data-initial-focus]");
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  try {
    target.focus({preventScroll: true});
  } catch {
    target.focus();
    window.scrollTo(scrollX, scrollY);
  }
}

function parseFragment(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  const root = template.content.firstElementChild;
  return {
    content: template.content,
    root: root instanceof HTMLElement ? root : null
  };
}

async function navigate(url, options = {}) {
  let html = "";
  try {
    html = await fetchFragment(url);
  } catch {
    window.location.href = url;
    return;
  }
  renderFragment(html, url, options);
}

async function fetchFragment(url, options = {}) {
  const response = await fetch(url, {
    headers: {[fragmentHeader]: "1"},
    signal: options.signal
  });
  if (!response.ok) {
    throw new Error(`fragment request failed: ${response.status}`);
  }
  return response.text();
}

function renderFragment(html, url, options = {}) {
  closeActivePlaylistMenu();
  const fragment = parseFragment(html);
  const nextPageRoot = fragment.root;
  if (!patchLibraryView(nextPageRoot)) {
    if (nextPageRoot) {
      view.replaceChildren(fragment.content);
    } else {
      view.innerHTML = html;
    }
  }
  const pageRoot = view.querySelector("[data-page]");
  const page = nextPageRoot ? nextPageRoot.dataset.page : pageRoot ? pageRoot.dataset.page : "";
  if (page) {
    view.dataset.page = page;
    document.body.dataset.page = page;
  }
  if (options.history !== false) {
    const method = options.replace ? "replaceState" : "pushState";
    if (!options.replace) {
      appHistoryDepth += 1;
    }
    const nextState = {kukichaDepth: appHistoryDepth};
    if (options.scroll === false) {
      nextState.kukichaScrollX = window.scrollX;
      nextState.kukichaScrollY = window.scrollY;
    } else {
      nextState.kukichaScrollX = 0;
      nextState.kukichaScrollY = 0;
    }
    history[method](nextState, "", url);
  }
  albumPlaybackCache.clear();
  hydrateVisibleTracks();
  updatePlaybackUi();
  syncFilterSummaries();
  syncRootForms();
  syncAlbumArtistMappingForms();
  localizeJobTimes();
  syncJobsStream();
  if (options.restoreScroll) {
    restoreScrollAfterRender(options.restoreScroll);
  } else if (options.scroll !== false) {
    scrollRestoreToken += 1;
    isRestoringScroll = false;
    if (!scrollToUrlHash(url)) {
      window.scrollTo(0, 0);
    }
    saveCurrentScrollState({anchor: null});
  } else {
    scrollRestoreToken += 1;
    isRestoringScroll = false;
  }
}

function scrollToUrlHash(url) {
  let hash = "";
  try {
    hash = new URL(url, window.location.href).hash;
  } catch {
    return false;
  }
  if (!hash || hash === "#") {
    return false;
  }
  let id = "";
  try {
    id = decodeURIComponent(hash.slice(1));
  } catch {
    return false;
  }
  const target = document.getElementById(id);
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  target.scrollIntoView({block: "start"});
  return true;
}

function patchLibraryView(nextPageRoot) {
  if (!(nextPageRoot instanceof HTMLElement)) {
    return false;
  }
  const nextPage = nextPageRoot.dataset.page || "";
  if (nextPage !== "library" && nextPage !== "playlists") {
    return false;
  }
  const currentPageRoot = view.querySelector("[data-page]");
  if (!(currentPageRoot instanceof HTMLElement) || currentPageRoot.dataset.page !== nextPage) {
    return false;
  }
  const currentResults = currentPageRoot.querySelector("[data-library-results]");
  const nextResults = nextPageRoot.querySelector("[data-library-results]");
  if (!(currentResults instanceof HTMLElement) || !(nextResults instanceof HTMLElement)) {
    return false;
  }
  syncLibraryFilterForm(currentPageRoot, nextPageRoot);
  currentResults.replaceChildren(...Array.from(nextResults.childNodes));
  syncLibraryPagination(currentPageRoot, nextPageRoot);
  return true;
}

function syncLibraryFilterForm(currentPageRoot, nextPageRoot) {
  const currentForm = currentPageRoot.querySelector("form[data-filter-form]");
  const nextForm = nextPageRoot.querySelector("form[data-filter-form]");
  if (!(currentForm instanceof HTMLFormElement) || !(nextForm instanceof HTMLFormElement)) {
    return;
  }
  syncFormControls(currentForm, nextForm);
}

function syncLibraryPagination(currentPageRoot, nextPageRoot) {
  const currentStatus = currentPageRoot.querySelector("[data-pagination-status]");
  const nextStatus = nextPageRoot.querySelector("[data-pagination-status]");
  if (currentStatus instanceof HTMLElement && nextStatus instanceof HTMLElement) {
    currentStatus.textContent = nextStatus.textContent;
  }
  syncPaginationLink(
    currentPageRoot.querySelector("[data-pagination-previous]"),
    nextPageRoot.querySelector("[data-pagination-previous]")
  );
  syncPaginationLink(
    currentPageRoot.querySelector("[data-pagination-next]"),
    nextPageRoot.querySelector("[data-pagination-next]")
  );
}

function syncPaginationLink(currentLink, nextLink) {
  if (!(currentLink instanceof HTMLAnchorElement) || !(nextLink instanceof HTMLAnchorElement)) {
    return;
  }
  currentLink.classList.toggle("disabled", nextLink.classList.contains("disabled"));
  if (nextLink.hasAttribute("href")) {
    currentLink.setAttribute("href", nextLink.getAttribute("href") || "");
  } else {
    currentLink.removeAttribute("href");
  }
  if (nextLink.getAttribute("aria-disabled") === "true") {
    currentLink.setAttribute("aria-disabled", "true");
  } else {
    currentLink.removeAttribute("aria-disabled");
  }
  if (nextLink.hasAttribute("tabindex")) {
    currentLink.setAttribute("tabindex", nextLink.getAttribute("tabindex") || "");
  } else {
    currentLink.removeAttribute("tabindex");
  }
  currentLink.textContent = nextLink.textContent;
}

function syncFormControls(currentForm, nextForm) {
  const nextControls = new Map();
  for (const control of nextForm.elements) {
    if (isSyncableFormControl(control) && control.name) {
      nextControls.set(formControlKey(control), control);
    }
  }
  for (const control of currentForm.elements) {
    if (!isSyncableFormControl(control) || !control.name) {
      continue;
    }
    const nextControl = nextControls.get(formControlKey(control));
    if (control instanceof HTMLInputElement && (control.type === "checkbox" || control.type === "radio")) {
      control.checked = nextControl instanceof HTMLInputElement ? nextControl.checked : false;
      continue;
    }
    control.value = nextControl ? nextControl.value : "";
  }
  for (const group of genreFilterGroups(currentForm)) {
    syncGenreFilterGroupState(group);
  }
}

function isSyncableFormControl(control) {
  return control instanceof HTMLInputElement
    || control instanceof HTMLSelectElement
    || control instanceof HTMLTextAreaElement;
}

function formControlKey(control) {
  const value = control instanceof HTMLInputElement
    && (control.type === "checkbox" || control.type === "radio")
    ? control.value
    : "";
  return `${control.tagName}:${control.name}:${value}`;
}

function genreFilterMenu(form) {
  const summary = form.querySelector('[data-filter-summary="genres"]');
  const menu = summary ? summary.closest(".filter-menu") : null;
  return menu instanceof HTMLElement ? menu : null;
}

function genreFilterGroups(form) {
  const menu = genreFilterMenu(form);
  return menu ? Array.from(menu.querySelectorAll(".filter-group")) : [];
}

function genreGroupParentInput(group) {
  const input = group.querySelector('.filter-option-parent input[data-genre-parent-control]');
  return input instanceof HTMLInputElement ? input : null;
}

function genreGroupParentParam(group) {
  const input = group.querySelector('input[data-genre-parent-param]');
  return input instanceof HTMLInputElement ? input : null;
}

function genreGroupStyleInputs(group) {
  return Array.from(group.querySelectorAll('.filter-option-child input[data-genre-child-control]'))
    .filter((input) => input instanceof HTMLInputElement);
}

function syncGenreFilterStates(form) {
  for (const group of genreFilterGroups(form)) {
    syncGenreFilterGroupState(group);
  }
}

function syncGenreFilterGroupState(group) {
  const parent = genreGroupParentInput(group);
  if (!(parent instanceof HTMLInputElement)) {
    return;
  }
  const styleInputs = genreGroupStyleInputs(group);
  const parentParam = genreGroupParentParam(group);
  if (!styleInputs.length) {
    parent.indeterminate = false;
    parent.dataset.genreState = parent.checked ? "genre" : "none";
    if (parentParam) {
      parentParam.disabled = !parent.checked;
    }
    return;
  }
  const checkedStyles = styleInputs.filter((input) => input.checked).length;
  parent.checked = checkedStyles === styleInputs.length;
  parent.indeterminate = checkedStyles > 0 && checkedStyles < styleInputs.length;
  parent.dataset.genreState = checkedStyles === styleInputs.length
    ? "all"
    : checkedStyles > 0
      ? "partial"
      : "none";
  if (parentParam) {
    parentParam.disabled = checkedStyles === 0;
  }
}

function syncGenreFilterControl(input, form) {
  const group = input.closest(".filter-group");
  if (!(group instanceof HTMLElement)) {
    return;
  }
  const parent = genreGroupParentInput(group);
  if (!(parent instanceof HTMLInputElement)) {
    return;
  }
  const styleInputs = genreGroupStyleInputs(group);
  if (!styleInputs.length) {
    const parentParam = genreGroupParentParam(group);
    if (parentParam) {
      parentParam.disabled = !parent.checked;
    }
    return;
  }
  if (input === parent) {
    const selectAll = parent.checked;
    parent.checked = selectAll;
    for (const styleInput of styleInputs) {
      styleInput.checked = selectAll;
    }
    syncGenreFilterGroupState(group);
    return;
  }
  if (styleInputs.includes(input)) {
    syncGenreFilterGroupState(group);
  }
}

function selectedGenreFilterCount(form) {
  let count = 0;
  for (const group of genreFilterGroups(form)) {
    const parent = genreGroupParentInput(group);
    if (!(parent instanceof HTMLInputElement)) {
      continue;
    }
    const styleInputs = genreGroupStyleInputs(group);
    if (!styleInputs.length) {
      count += parent.checked ? 1 : 0;
      continue;
    }
    let checkedStyles = 0;
    for (const styleInput of styleInputs) {
      if (styleInput.checked) {
        checkedStyles += 1;
      }
    }
    count += checkedStyles || (parent.checked ? 1 : 0);
  }
  return count;
}

function collapsedGenreChildParamNames(form) {
  const names = new Set();
  for (const group of genreFilterGroups(form)) {
    const parent = genreGroupParentInput(group);
    if (!(parent instanceof HTMLInputElement) || !parent.checked) {
      continue;
    }
    const styleInputs = genreGroupStyleInputs(group);
    if (!styleInputs.length || !styleInputs.every((input) => input.checked)) {
      continue;
    }
    for (const styleInput of styleInputs) {
      if (styleInput.name) {
        names.add(styleInput.name);
      }
    }
  }
  return names;
}

function syncFilterSummaries(form = view.querySelector("form[data-filter-form]")) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  syncGenreFilterStates(form);
  updateFilterSummary(form, "roots", checkedInputCount(form, "root"));
  updateFilterSummary(form, "artists", checkedInputCount(form, "artist"));
  updateFilterSummary(form, "genres", selectedGenreFilterCount(form));
  updateFilterSummary(form, "properties", selectedPropertyCount(form));
}

function checkedInputCount(form, name) {
  return form.querySelectorAll(`input[name="${name}"]:checked`).length;
}

function selectedPropertyCount(form) {
  let count = 0;
  for (const name of ["has_cover", "compilation", "work"]) {
    const input = form.querySelector(`input[name="${name}"]:checked`);
    if (input instanceof HTMLInputElement && input.value) {
      count += 1;
    }
  }
  return count;
}

function updateFilterSummary(form, key, count) {
  const summary = form.querySelector(`[data-filter-summary="${key}"]`);
  if (!(summary instanceof HTMLElement)) {
    return;
  }
  const label = summary.dataset.summaryLabel || "";
  summary.textContent = count ? `${label}: ${count}` : label;
}

function sameOrigin(url) {
  return url.origin === window.location.origin;
}

function saveCurrentScrollState(options = {}) {
  cancelPendingScrollStateSave();
  const currentState = history.state && typeof history.state === "object" ? history.state : {};
  const nextState = {
    ...currentState,
    kukichaDepth: appHistoryDepth,
    kukichaScrollX: window.scrollX,
    kukichaScrollY: window.scrollY
  };

  if (Object.prototype.hasOwnProperty.call(options, "anchor")) {
    if (options.anchor) {
      nextState.kukichaAnchorHref = options.anchor.href;
      nextState.kukichaAnchorTop = options.anchor.top;
    } else {
      delete nextState.kukichaAnchorHref;
      delete nextState.kukichaAnchorTop;
    }
  }

  try {
    history.replaceState(nextState, "", window.location.href);
  } catch {
    return;
  }
}

function scheduleScrollStateSave() {
  if (isRestoringScroll || scrollSaveFrame) {
    return;
  }
  scrollSaveFrame = requestAnimationFrame(() => {
    scrollSaveFrame = 0;
    saveCurrentScrollState({anchor: null});
  });
}

function cancelPendingScrollStateSave() {
  if (!scrollSaveFrame) {
    return;
  }
  cancelAnimationFrame(scrollSaveFrame);
  scrollSaveFrame = 0;
}

function scrollAnchorForLink(link) {
  const bounds = link.getBoundingClientRect();
  return {
    href: new URL(link.href, window.location.origin).href,
    top: bounds.top
  };
}

function scrollStateFromHistory(state) {
  if (!state || typeof state !== "object") {
    return {x: 0, y: 0, anchorHref: "", anchorTop: 0};
  }
  const x = Number(state.kukichaScrollX);
  const y = Number(state.kukichaScrollY);
  const anchorTop = Number(state.kukichaAnchorTop);
  return {
    x: Number.isFinite(x) ? x : 0,
    y: Number.isFinite(y) ? y : 0,
    anchorHref: typeof state.kukichaAnchorHref === "string" ? state.kukichaAnchorHref : "",
    anchorTop: Number.isFinite(anchorTop) ? anchorTop : 0
  };
}

function restoreScrollAfterRender(scrollState) {
  const token = scrollRestoreToken + 1;
  scrollRestoreToken = token;
  isRestoringScroll = true;
  let finished = false;
  const finish = () => {
    if (finished || token !== scrollRestoreToken) {
      return;
    }
    finished = true;
    applyScrollState(scrollState);
    isRestoringScroll = false;
    saveCurrentScrollState({anchor: null});
  };

  requestAnimationFrame(() => {
    if (token !== scrollRestoreToken) {
      return;
    }
    applyScrollState(scrollState);
    requestAnimationFrame(() => {
      if (token !== scrollRestoreToken) {
        return;
      }
      applyScrollState(scrollState);
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(finish, finish);
      }
      setTimeout(finish, 75);
    });
  });
}

function applyScrollState(scrollState) {
  window.scrollTo(scrollState.x, scrollState.y);
  if (!scrollState.anchorHref) {
    return;
  }
  const anchor = albumAnchorByHref(scrollState.anchorHref);
  if (!anchor) {
    return;
  }
  const delta = anchor.getBoundingClientRect().top - scrollState.anchorTop;
  if (delta) {
    window.scrollBy(0, delta);
  }
}

function albumAnchorByHref(href) {
  for (const link of view.querySelectorAll(".album-card-cover[href]")) {
    if (new URL(link.href, window.location.origin).href === href) {
      return link;
    }
  }
  return null;
}

function albumCardAnchor(link) {
  const albumCard = link.closest(".album-card");
  if (!(albumCard instanceof Element)) {
    return link;
  }
  const anchor = albumCard.querySelector(".album-card-cover[href]");
  return anchor instanceof HTMLAnchorElement ? anchor : link;
}

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  const closeToastButton = event.target.closest("[data-close-toast]");
  if (closeToastButton) {
    event.preventDefault();
    closeToast(closeToastButton);
    return;
  }
  const closeJobToastButton = event.target.closest("[data-close-job-toast]");
  if (closeJobToastButton) {
    event.preventDefault();
    closeJobToast(closeJobToastButton);
    return;
  }
  const cancelJobButton = event.target.closest("[data-cancel-job]");
  if (cancelJobButton) {
    event.preventDefault();
    void cancelJob(cancelJobButton);
    return;
  }
  const rescanLibraryButton = event.target.closest("[data-rescan-library]");
  if (rescanLibraryButton) {
    event.preventDefault();
    rescanLibrary(rescanLibraryButton);
    return;
  }
  const deleteRootButton = event.target.closest("[data-delete-root]");
  if (deleteRootButton) {
    event.preventDefault();
    deleteRoot(deleteRootButton);
    return;
  }
  const deleteMusicBrainzOverrideButton = event.target.closest("[data-delete-musicbrainz-override]");
  if (deleteMusicBrainzOverrideButton) {
    event.preventDefault();
    void deleteMusicBrainzOverride(deleteMusicBrainzOverrideButton);
    return;
  }
  const editAlbumArtistMappingButton = event.target.closest("[data-edit-album-artist-mapping]");
  if (editAlbumArtistMappingButton) {
    event.preventDefault();
    editAlbumArtistMapping(editAlbumArtistMappingButton);
    return;
  }
  const queueAlbum = event.target.closest("[data-queue-album]");
  if (queueAlbum) {
    event.preventDefault();
    void queueAlbumFromGrid(queueAlbum);
    return;
  }
  const queueTrack = event.target.closest("[data-queue-track]");
  if (queueTrack) {
    event.preventDefault();
    const row = queueTrack.closest("tr[data-track-id]");
    if (row) {
      void appendTrackToQueue(trackFromRow(row));
    }
    return;
  }
  const deleteQueueTrack = event.target.closest("[data-delete-queue-track]");
  if (deleteQueueTrack) {
    event.preventDefault();
    const row = deleteQueueTrack.closest("tr[data-queue-position]");
    if (row) {
      void deleteQueueTrackFromQueue(row);
    }
    return;
  }
  const playAlbum = event.target.closest("[data-play-album]");
  if (playAlbum) {
    event.preventDefault();
    void playAlbumFromGrid(playAlbum);
    return;
  }

  const playTrack = event.target.closest("[data-play-track]");
  if (playTrack) {
    event.preventDefault();
    const row = playTrack.closest("tr[data-track-id]");
    if (row) {
      playFromRow(row);
    }
    return;
  }

  const historyBack = event.target.closest("a[data-history-back]");
  if (historyBack && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !historyBack.target) {
    const url = new URL(historyBack.href);
    if (!sameOrigin(url)) {
      return;
    }
    event.preventDefault();
    saveCurrentScrollState({anchor: null});
    if (appHistoryDepth > 0) {
      history.back();
    } else {
      navigate(url);
    }
    return;
  }

  const link = event.target.closest("a[data-nav]");
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target) {
    return;
  }
  if (link.classList.contains("disabled") || link.getAttribute("aria-disabled") === "true" || !link.hasAttribute("href")) {
    event.preventDefault();
    return;
  }
  const url = new URL(link.href);
  if (!sameOrigin(url)) {
    return;
  }
  event.preventDefault();
  const scrollAnchor = link.hasAttribute("data-album-nav")
    ? scrollAnchorForLink(albumCardAnchor(link))
    : null;
  saveCurrentScrollState({anchor: scrollAnchor});
  navigate(url);
});

document.addEventListener("dblclick", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  if (event.target.closest("button, a, input, label, summary")) {
    return;
  }
  const row = event.target.closest("tr[data-track-id]");
  if (!row) {
    return;
  }
  event.preventDefault();
  playFromRow(row);
});

document.addEventListener("submit", (event) => {
  const albumMusicBrainzForm = event.target.closest("form[data-album-musicbrainz-form]");
  if (albumMusicBrainzForm) {
    event.preventDefault();
    submitAlbumMusicBrainzForm(albumMusicBrainzForm);
    return;
  }
  const albumTagForm = event.target.closest("form[data-album-tag-form]");
  if (albumTagForm) {
    event.preventDefault();
    submitAlbumTagForm(albumTagForm);
    return;
  }
  const rootForm = event.target.closest("form[data-root-form]");
  if (rootForm) {
    event.preventDefault();
    submitRootForm(rootForm);
    return;
  }
  const albumArtistMappingForm = event.target.closest("form[data-album-artist-mapping-form]");
  if (albumArtistMappingForm) {
    event.preventDefault();
    submitAlbumArtistMappingForm(albumArtistMappingForm);
    return;
  }
  const form = event.target.closest("form[data-filter-form]");
  if (!form) {
    return;
  }
  event.preventDefault();
  syncFilterSummaries(form);
  saveCurrentScrollState({anchor: null});
  navigate(formUrl(form));
});

document.addEventListener("change", (event) => {
  const playlistToggle = event.target instanceof HTMLInputElement
    ? event.target.closest("[data-playlist-toggle]")
    : null;
  if (playlistToggle) {
    void toggleTrackPlaylist(playlistToggle);
    return;
  }

  const form = event.target.closest("form[data-filter-form]");
  if (!form || !(event.target instanceof HTMLInputElement)) {
    return;
  }
  syncGenreFilterControl(event.target, form);
  if (event.target.type === "search") {
    return;
  }
  syncFilterSummaries(form);
  saveCurrentScrollState({anchor: null});
  navigate(formUrl(form));
});

document.addEventListener("input", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  const albumArtistMappingForm = event.target.closest("form[data-album-artist-mapping-form]");
  if (albumArtistMappingForm) {
    syncAlbumArtistMappingFormState(albumArtistMappingForm);
    return;
  }
  const form = event.target.closest("form[data-root-form]");
  if (!form || !(event.target instanceof HTMLInputElement)) {
    return;
  }
  syncRootFormState(form);
});

document.addEventListener("toggle", (event) => {
  if (!(event.target instanceof HTMLDetailsElement) || !event.target.matches(dropdownMenuSelector)) {
    return;
  }
  if (event.target.matches("[data-playlist-menu]") && !event.target.open) {
    restorePlaylistMenuOptions(event.target);
    return;
  }
  if (!event.target.open) {
    return;
  }
  if (event.target.matches("[data-playlist-menu]")) {
    openPlaylistMenu(event.target);
  }
  document.querySelectorAll(`${dropdownMenuSelector}[open]`).forEach((details) => {
    if (details !== event.target) {
      details.open = false;
    }
  });
}, true);

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  if (event.target.closest(dropdownMenuSelector) || event.target.closest("[data-playlist-options]")) {
    return;
  }
  document.querySelectorAll(`${dropdownMenuSelector}[open]`).forEach((details) => {
    details.open = false;
  });
});

window.addEventListener("popstate", (event) => {
  cancelPendingScrollStateSave();
  appHistoryDepth = initialAppHistoryDepth();
  navigate(window.location.href, {
    history: false,
    restoreScroll: scrollStateFromHistory(event.state),
    scroll: false
  });
});

window.addEventListener("scroll", () => {
  positionActivePlaylistMenu();
  scheduleScrollStateSave();
}, {passive: true});

window.addEventListener("resize", () => {
  positionActivePlaylistMenu();
}, {passive: true});

window.addEventListener("pagehide", () => {
  saveCurrentScrollState();
  closeJobsStream();
});

window.addEventListener("pageshow", () => {
  syncJobsStream();
});

window.addEventListener("beforeunload", () => {
  closeJobsStream();
});

playButton.addEventListener("click", () => {
  togglePlayback();
});

function togglePlayback() {
  const loadedId = loadedTrackId();
  if (loadedId !== null && playbackIsActive()) {
    manualPauseRequested = true;
    clearPendingPauseCommit();
    clearPauseStateSuppression();
    audio.pause();
    return;
  }
  const controlsPosition = queuePositionForControls();
  const queuedTrackId = queueState.track_ids[controlsPosition] ?? null;
  const firstPlayablePosition = nextPlayableQueuePosition(-1);
  const trackId = loadedId !== null && !trackHasPlaybackError(loadedId)
    ? loadedId
    : queuedTrackId !== null && !trackHasPlaybackError(queuedTrackId)
      ? queuedTrackId
      : firstPlayablePosition === -1
        ? null
        : queueState.track_ids[firstPlayablePosition];
  if (trackId === null) {
    return;
  }
  playTrack(trackById(trackId), {restart: false});
}

function clearPauseStateSuppression() {
  suppressPauseStateUntilPlay = false;
}

function clearPendingPauseCommit() {
  if (!pendingPauseCommitTimeout) {
    return;
  }
  clearTimeout(pendingPauseCommitTimeout);
  pendingPauseCommitTimeout = 0;
}

function commitPausedState() {
  clearPendingPauseCommit();
  queueState.paused = true;
  postPlayback({paused: true, loaded_track_id: loadedTrackId()});
  updatePlaybackUi();
}

function schedulePauseCommit() {
  clearPendingPauseCommit();
  pendingPauseCommitTimeout = window.setTimeout(() => {
    pendingPauseCommitTimeout = 0;
    if (!audio.paused) {
      return;
    }
    if (suppressPauseStateUntilPlay || audio.seeking) {
      schedulePauseCommit();
      return;
    }
    queueState.paused = true;
    postPlayback({paused: true, loaded_track_id: loadedTrackId()});
    updatePlaybackUi();
  }, 180);
}

function playbackPausedForUi() {
  return queueState.paused || (
    audio.paused
    && !suppressPauseStateUntilPlay
    && !pendingPauseCommitTimeout
  );
}

function playbackIsActive() {
  const loadedId = loadedTrackId();
  return loadedId !== null && !trackHasPlaybackError(loadedId) && !playbackPausedForUi();
}

previousButton.addEventListener("click", () => {
  moveQueue(-1);
});

nextButton.addEventListener("click", () => {
  moveQueue(1);
});

audio.addEventListener("seeking", () => {
  if (loadedTrackId() === null || queueState.paused) {
    return;
  }
  suppressPauseStateUntilPlay = true;
  clearPendingPauseCommit();
  updatePlaybackUi();
});

audio.addEventListener("seeked", () => {
  if (!suppressPauseStateUntilPlay) {
    return;
  }
  suppressPauseStateUntilPlay = false;
  if (audio.paused) {
    schedulePauseCommit();
    updatePlaybackUi();
    return;
  }
  updatePlaybackUi();
});

audio.addEventListener("play", () => {
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  queueState.paused = false;
  postPlayback({paused: false, loaded_track_id: loadedTrackId()});
  updatePlaybackUi();
});

audio.addEventListener("pause", () => {
  if (manualPauseRequested) {
    manualPauseRequested = false;
    clearPauseStateSuppression();
    commitPausedState();
    return;
  }
  schedulePauseCommit();
  updatePlaybackUi();
});

audio.addEventListener("error", () => {
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  const track = trackById(loadedTrackId());
  handlePlaybackFailure(track, playbackErrorMessage(track));
});

audio.addEventListener("ended", () => {
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  const position = queuePositionForControls();
  const nextPosition = nextPlayableQueuePosition(position);
  if (nextPosition !== -1) {
    playQueuePosition(nextPosition);
    return;
  }
  queueState.position = queueState.track_ids.length;
  queueState.paused = true;
  postPlayback({
    position: queueState.position,
    paused: true,
    errored_track_ids: queueState.errored_track_ids
  });
  updatePlaybackUi();
});

function formUrl(form) {
  const url = new URL(form.action, window.location.origin);
  syncGenreFilterStates(form);
  const collapsedChildParamNames = collapsedGenreChildParamNames(form);
  const defaultSort = form.dataset.defaultSort || "";
  const data = new FormData(form);
  data.delete("page");
  for (const [key, value] of data.entries()) {
    if (collapsedChildParamNames.has(key)) {
      continue;
    }
    if (key === "sort" && String(value) === defaultSort) {
      continue;
    }
    if (String(value).trim()) {
      url.searchParams.append(key, value);
    }
  }
  return url;
}

function syncRootFormState(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const input = form.querySelector("[data-root-path-input]");
  const button = form.querySelector("[data-add-root]");
  if (!(input instanceof HTMLInputElement) || !(button instanceof HTMLButtonElement)) {
    return;
  }
  button.disabled = !input.value.trim();
}

function syncRootForms() {
  view.querySelectorAll("form[data-root-form]").forEach((form) => {
    syncRootFormState(form);
  });
}

function normalizedAlbumArtistMappingText(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n");
}

function syncAlbumArtistMappingFormState(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const sourceInput = form.querySelector("[data-album-artist-mapping-source]");
  const artistsInput = form.querySelector("[data-album-artist-mapping-artists]");
  const button = form.querySelector("[data-save-album-artist-mapping]");
  if (!(sourceInput instanceof HTMLInputElement)
    || !(artistsInput instanceof HTMLTextAreaElement)
    || !(button instanceof HTMLButtonElement)) {
    return;
  }
  button.disabled = !sourceInput.value.trim()
    || !normalizedAlbumArtistMappingText(artistsInput.value);
}

function syncAlbumArtistMappingForms() {
  view.querySelectorAll("form[data-album-artist-mapping-form]").forEach((form) => {
    syncAlbumArtistMappingFormState(form);
  });
}

function editAlbumArtistMapping(button) {
  if (!(button instanceof Element)) {
    return;
  }
  const card = button.closest("[data-mapping-card]");
  if (!(card instanceof HTMLElement)) {
    return;
  }
  const source = card.querySelector(".mapping-card-source");
  const artists = card.querySelector(".mapping-card-artists");
  const copy = card.querySelector(".mapping-card-copy");
  if (!(source instanceof HTMLElement) || !(artists instanceof HTMLElement) || !(copy instanceof HTMLElement)) {
    return;
  }

  const albumArtist = source.textContent ? source.textContent.trim() : "";
  const mappedArtists = normalizedAlbumArtistMappingText(artists.textContent || "");
  if (!albumArtist) {
    return;
  }

  view.querySelectorAll("[data-mapping-card].active").forEach((activeCard) => {
    if (activeCard !== card && activeCard instanceof HTMLElement) {
      restoreAlbumArtistMappingCard(activeCard);
    }
  });
  const existingForm = card.querySelector("form[data-album-artist-mapping-form]");
  if (existingForm instanceof HTMLFormElement) {
    const existingTextarea = existingForm.querySelector("[data-album-artist-mapping-artists]");
    if (existingTextarea instanceof HTMLTextAreaElement) {
      existingTextarea.focus();
    }
    return;
  }

  const form = albumArtistMappingEditorForm(albumArtist, mappedArtists);
  artists.hidden = true;
  copy.append(form);
  card.classList.add("active");
  syncAlbumArtistMappingFormState(form);
  const artistsInput = form.querySelector("[data-album-artist-mapping-artists]");
  if (!(artistsInput instanceof HTMLTextAreaElement)) {
    return;
  }
  try {
    artistsInput.focus({preventScroll: true});
  } catch {
    artistsInput.focus();
  }
}

function albumArtistMappingEditorForm(albumArtist, mappedArtists) {
  const form = document.createElement("form");
  form.className = "mapping-edit-form";
  form.action = "/api/album-artist-mappings";
  form.method = "post";
  form.dataset.albumArtistMappingForm = "";

  const sourceInput = document.createElement("input");
  sourceInput.type = "hidden";
  sourceInput.name = "album_artist";
  sourceInput.value = albumArtist;
  sourceInput.dataset.albumArtistMappingSource = "";

  const field = document.createElement("label");
  field.className = "settings-field settings-field-wide mapping-edit-field";

  const label = document.createElement("span");
  label.className = "settings-label";
  label.textContent = "Mapped artists";

  const textarea = document.createElement("textarea");
  textarea.className = "settings-input settings-textarea";
  textarea.name = "mapped_artists";
  textarea.rows = 6;
  textarea.spellcheck = false;
  textarea.value = mappedArtists;
  textarea.dataset.albumArtistMappingArtists = "";

  field.append(label, textarea);

  const actions = document.createElement("div");
  actions.className = "settings-actions";

  const submit = document.createElement("button");
  submit.className = "primary";
  submit.type = "submit";
  submit.textContent = "Save Mapping";
  submit.dataset.saveAlbumArtistMapping = "";
  actions.append(submit);

  const status = document.createElement("div");
  status.className = "settings-status";
  status.dataset.albumArtistMappingStatus = "";
  status.setAttribute("aria-live", "polite");

  form.append(sourceInput, field, actions, status);
  return form;
}

function restoreAlbumArtistMappingCard(card) {
  const artists = card.querySelector(".mapping-card-artists");
  const form = card.querySelector("form[data-album-artist-mapping-form]");
  if (artists instanceof HTMLElement) {
    artists.hidden = false;
  }
  if (form instanceof HTMLFormElement) {
    form.remove();
  }
  card.classList.remove("active");
}

function openPlaylistMenu(menu) {
  if (!(menu instanceof HTMLDetailsElement)) {
    return;
  }
  if (activePlaylistMenu && activePlaylistMenu !== menu) {
    activePlaylistMenu.open = false;
    restorePlaylistMenuOptions(activePlaylistMenu);
  }
  const sourceOptions = menu.querySelector("[data-playlist-options]");
  if (!(sourceOptions instanceof HTMLElement)) {
    return;
  }
  const options = playlistFloatingOptions();
  activePlaylistMenu = menu;
  activePlaylistSourceOptions = sourceOptions;
  activePlaylistOptions = options;
  options.replaceChildren(...Array.from(sourceOptions.children).map((child) => child.cloneNode(true)));
  options.hidden = false;
  positionActivePlaylistMenu();
}

function playlistFloatingOptions() {
  let options = document.querySelector("[data-playlist-floating-options]");
  if (!(options instanceof HTMLElement)) {
    options = document.createElement("div");
    options.className = "filter-options playlist-track-options playlist-track-options-floating";
    options.dataset.playlistOptions = "";
    options.dataset.playlistFloatingOptions = "";
    options.hidden = true;
    document.body.append(options);
  }
  return options;
}

function restorePlaylistMenuOptions(menu = activePlaylistMenu) {
  if (!(menu instanceof HTMLDetailsElement) || menu !== activePlaylistMenu) {
    return;
  }
  if (activePlaylistOptions) {
    activePlaylistOptions.hidden = true;
    activePlaylistOptions.replaceChildren();
    activePlaylistOptions.style.left = "";
    activePlaylistOptions.style.top = "";
    activePlaylistOptions.style.right = "";
    activePlaylistOptions.style.bottom = "";
    activePlaylistOptions.style.width = "";
    activePlaylistOptions.style.maxHeight = "";
  }
  activePlaylistMenu = null;
  activePlaylistOptions = null;
  activePlaylistSourceOptions = null;
}

function closeActivePlaylistMenu() {
  if (activePlaylistMenu) {
    activePlaylistMenu.open = false;
  }
  restorePlaylistMenuOptions(activePlaylistMenu);
}

function positionActivePlaylistMenu() {
  if (!(activePlaylistMenu instanceof HTMLDetailsElement) || !(activePlaylistOptions instanceof HTMLElement)) {
    return;
  }
  const summary = activePlaylistMenu.querySelector("summary");
  if (!(summary instanceof HTMLElement)) {
    return;
  }
  const bounds = summary.getBoundingClientRect();
  const margin = 8;
  const width = Math.min(280, Math.max(180, window.innerWidth - 32));
  const spaceBelow = window.innerHeight - bounds.bottom - margin;
  const spaceAbove = bounds.top - margin;
  const opensAbove = spaceBelow < 160 && spaceAbove > spaceBelow;
  const maxHeight = Math.max(120, opensAbove ? spaceAbove - 4 : spaceBelow - 4);
  activePlaylistOptions.style.width = `${width}px`;
  activePlaylistOptions.style.left = `${Math.max(16, Math.min(window.innerWidth - width - 16, bounds.right - width))}px`;
  activePlaylistOptions.style.maxHeight = `${maxHeight}px`;
  if (opensAbove) {
    activePlaylistOptions.style.top = "";
    activePlaylistOptions.style.bottom = `${window.innerHeight - bounds.top + 4}px`;
  } else {
    activePlaylistOptions.style.top = `${bounds.bottom + 4}px`;
    activePlaylistOptions.style.bottom = "";
  }
}

async function toggleTrackPlaylist(input) {
  if (!(input instanceof HTMLInputElement) || input.disabled) {
    return;
  }
  const trackId = Number(input.dataset.trackId || "");
  const playlistId = Number(input.dataset.playlistId || "");
  if (!Number.isInteger(trackId) || trackId <= 0 || !Number.isInteger(playlistId) || playlistId <= 0) {
    return;
  }

  const requestedChecked = input.checked;
  input.disabled = true;
  syncPlaylistToggleState(input);
  updatePlaylistBookmarkFromToggle(input);
  try {
    const response = await fetch(
      `/api/tracks/${encodeURIComponent(trackId)}/playlists/${encodeURIComponent(playlistId)}`,
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({checked: requestedChecked})
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to update playlist.";
      throw new Error(message);
    }
    input.checked = payload && payload.checked === true;
    syncPlaylistToggleState(input);
    updatePlaylistBookmarkFromToggle(input);
    if (payload && payload.job) {
      showJobToast(payload.job);
    }
    input.dispatchEvent(new CustomEvent("kukicha:playlist-updated", {bubbles: true}));
    await refreshVisiblePlaylistPage(playlistId);
  } catch (error) {
    input.checked = !requestedChecked;
    syncPlaylistToggleState(input);
    updatePlaylistBookmarkFromToggle(input);
    showToast(error instanceof Error && error.message ? error.message : "Unable to update playlist.", {error: true});
  } finally {
    if (input.isConnected) {
      input.disabled = false;
    }
  }
}

function syncPlaylistToggleState(input) {
  const trackId = input.dataset.trackId || "";
  const playlistId = input.dataset.playlistId || "";
  if (!trackId || !playlistId) {
    return;
  }
  const selector = `[data-playlist-toggle][data-track-id="${cssEscape(trackId)}"][data-playlist-id="${cssEscape(playlistId)}"]`;
  const options = input.closest("[data-playlist-options]");
  const syncInput = (target) => {
    if (!(target instanceof HTMLInputElement) || target === input) {
      return;
    }
    target.checked = input.checked;
    if (input.checked) {
      target.setAttribute("checked", "");
    } else {
      target.removeAttribute("checked");
    }
  };
  if (options === activePlaylistOptions && activePlaylistSourceOptions) {
    syncInput(activePlaylistSourceOptions.querySelector(selector));
  } else if (options === activePlaylistSourceOptions && activePlaylistOptions) {
    syncInput(activePlaylistOptions.querySelector(selector));
  }
  if (input.checked) {
    input.setAttribute("checked", "");
  } else {
    input.removeAttribute("checked");
  }
}

function cssEscape(value) {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

function updatePlaylistBookmarkFromToggle(input) {
  const options = input.closest("[data-playlist-options]");
  if (!(options instanceof HTMLElement)) {
    return;
  }
  const hasPlaylistMembership = Array.from(options.querySelectorAll("[data-playlist-toggle]"))
    .some((element) => element instanceof HTMLInputElement && element.checked);
  const menu = activePlaylistOptions === options
    ? activePlaylistMenu
    : options.closest("[data-playlist-menu]");
  setPlaylistBookmarkState(menu, hasPlaylistMembership);
}

function setPlaylistBookmarkState(menu, hasPlaylistMembership) {
  if (!(menu instanceof HTMLDetailsElement)) {
    return;
  }
  menu.classList.toggle("has-playlist-membership", hasPlaylistMembership);
  const icon = menu.querySelector(".playlist-icon");
  if (icon instanceof SVGElement) {
    icon.classList.toggle("playlist-icon-filled", hasPlaylistMembership);
    const shape = icon.querySelector(".playlist-icon-shape");
    if (shape instanceof SVGElement) {
      shape.setAttribute("fill", hasPlaylistMembership ? "currentColor" : "none");
    }
  }
}

async function refreshVisiblePlaylistPage(playlistId) {
  if (document.body.dataset.page !== "playlist") {
    return;
  }
  const path = window.location.pathname.replace(/\/+$/, "");
  if (path !== `/playlists/${playlistId}`) {
    return;
  }
  try {
    const html = await fetchFragment(window.location.href);
    renderFragment(html, window.location.href, {
      history: false,
      scroll: false
    });
  } catch {
    return;
  }
}

function syncJobsStream() {
  if (typeof EventSource !== "function" || jobsSource) {
    return;
  }
  if (document.readyState !== "complete") {
    if (!jobsStreamLoadPending) {
      jobsStreamLoadPending = true;
      window.addEventListener("load", () => {
        jobsStreamLoadPending = false;
        syncJobsStream();
      }, {once: true});
    }
    return;
  }
  jobsSource = new EventSource("/api/jobs/events");
  jobsSource.addEventListener("job", (event) => {
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    showJobToast(payload);
  });
  jobsSource.addEventListener("error", () => {
    if (jobsSource && jobsSource.readyState === EventSource.CLOSED) {
      closeJobsStream();
    }
  });
}

function closeJobsStream() {
  if (!jobsSource) {
    return;
  }
  jobsSource.close();
  jobsSource = null;
}

function localizeJobTimes() {
  view.querySelectorAll("[data-job-time]").forEach((element) => {
    if (!(element instanceof HTMLTimeElement)) {
      return;
    }
    const timestamp = element.dateTime || element.getAttribute("datetime") || "";
    element.textContent = formatBrowserDateTime(timestamp, element.textContent || timestamp);
  });
}

function formatBrowserDateTime(value, fallback = "") {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return fallback || value;
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "medium"
    }).format(parsed);
  } catch {
    return fallback || value;
  }
}

async function submitAlbumMusicBrainzForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const releaseMbidInput = form.querySelector("[data-musicbrainz-release-mbid-input]");
  const releaseGroupMbidInput = form.querySelector("[data-musicbrainz-release-group-mbid-input]");
  const submitButton = form.querySelector("[data-save-album-musicbrainz]");
  if (
    !(releaseMbidInput instanceof HTMLInputElement)
    || !(releaseGroupMbidInput instanceof HTMLInputElement)
    || !(submitButton instanceof HTMLButtonElement)
  ) {
    return;
  }

  setAlbumMusicBrainzStatus(form, "Submitting MusicBrainz edit...");
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  releaseMbidInput.disabled = true;
  releaseGroupMbidInput.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        musicbrainz_release_mbid: releaseMbidInput.value.trim(),
        musicbrainz_release_group_mbid: releaseGroupMbidInput.value.trim()
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to save MusicBrainz IDs.";
      setAlbumMusicBrainzStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "MusicBrainz ID edit queued.";
    setAlbumMusicBrainzStatus(form, message);
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    setAlbumMusicBrainzStatus(form, "Unable to save MusicBrainz IDs.", true);
    showToast("Unable to save MusicBrainz IDs.", {error: true});
  } finally {
    submitButton.removeAttribute("aria-busy");
    submitButton.disabled = false;
    releaseMbidInput.disabled = false;
    releaseGroupMbidInput.disabled = false;
  }
}

async function submitAlbumTagForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const genreInput = form.querySelector("[data-album-genre-input]");
  const albumArtistInput = form.querySelector("[data-album-artist-input]");
  const submitButton = form.querySelector("[data-save-album-tag]");
  const trackRows = Array.from(form.querySelectorAll("[data-track-tag-row]"));
  const trackTagInputs = Array.from(
    form.querySelectorAll("[data-track-artist-input], [data-track-album-input]")
  );
  if (
    !(genreInput instanceof HTMLInputElement)
    || !(albumArtistInput instanceof HTMLInputElement)
    || !(submitButton instanceof HTMLButtonElement)
  ) {
    return;
  }

  const tracks = trackRows.map((row) => {
    const artistInput = row.querySelector("[data-track-artist-input]");
    const albumInput = row.querySelector("[data-track-album-input]");
    if (
      !(artistInput instanceof HTMLInputElement)
      || !(albumInput instanceof HTMLInputElement)
    ) {
      return null;
    }
    return {
      track_id: Number(row.dataset.trackId || ""),
      artist: artistInput.value.trim(),
      album: albumInput.value.trim()
    };
  }).filter((item) => item && Number.isInteger(item.track_id) && item.track_id > 0);
  if (!tracks.length) {
    setAlbumTagStatus(form, "No tracks available to edit.", true);
    return;
  }

  setAlbumTagStatus(form, "Submitting tag edit...");
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  trackTagInputs.forEach((input) => {
    if (input instanceof HTMLInputElement) {
      input.disabled = true;
    }
  });
  genreInput.disabled = true;
  albumArtistInput.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        genre: genreInput.value.trim(),
        album_artist: albumArtistInput.value.trim(),
        tracks
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to edit tags.";
      setAlbumTagStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Tag edit queued.";
    setAlbumTagStatus(form, message);
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    setAlbumTagStatus(form, "Unable to edit tags.", true);
    showToast("Unable to edit tags.", {error: true});
  } finally {
    submitButton.removeAttribute("aria-busy");
    submitButton.disabled = false;
    trackTagInputs.forEach((input) => {
      if (input instanceof HTMLInputElement) {
        input.disabled = false;
      }
    });
    genreInput.disabled = false;
    albumArtistInput.disabled = false;
  }
}

async function submitRootForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const input = form.querySelector("[data-root-path-input]");
  const submitButton = form.querySelector("[data-add-root]");
  const status = form.querySelector("[data-root-form-status]");
  if (!(input instanceof HTMLInputElement) || !(submitButton instanceof HTMLButtonElement)) {
    return;
  }

  const path = input.value.trim();
  if (!path) {
    syncRootFormState(form);
    return;
  }

  setRootFormStatus(status, "Submitting scan...");
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to add and scan root.";
      setRootFormStatus(status, message, true);
      showToast(message, {error: true});
      return;
    }
    input.value = "";
    syncRootFormState(form);
    setRootFormStatus(status, "");
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Add and scan queued.";
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    setRootFormStatus(status, "Unable to add and scan root.", true);
    showToast("Unable to add and scan root.", {error: true});
  } finally {
    submitButton.removeAttribute("aria-busy");
    syncRootFormState(form);
  }
}

async function submitAlbumArtistMappingForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const sourceInput = form.querySelector("[data-album-artist-mapping-source]");
  const artistsInput = form.querySelector("[data-album-artist-mapping-artists]");
  const submitButton = form.querySelector("[data-save-album-artist-mapping]");
  if (!(sourceInput instanceof HTMLInputElement)
    || !(artistsInput instanceof HTMLTextAreaElement)
    || !(submitButton instanceof HTMLButtonElement)) {
    return;
  }

  const albumArtist = sourceInput.value.trim();
  const mappedArtists = normalizedAlbumArtistMappingText(artistsInput.value);
  if (!albumArtist || !mappedArtists) {
    syncAlbumArtistMappingFormState(form);
    return;
  }

  artistsInput.value = mappedArtists;
  setAlbumArtistMappingStatus(form, "Saving mapping...");
  artistsInput.disabled = true;
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        album_artist: albumArtist,
        mapped_artists: mappedArtists
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to save mapping.";
      setAlbumArtistMappingStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Mapping saved. Rescan the library to update library filters, artists, and stats.";
    finishAlbumArtistMappingEdit(form, albumArtist, mappedArtists);
    showToast(message, {link: rescanSettingsLinkForMessage(message)});
    return;
  } catch {
    setAlbumArtistMappingStatus(form, "Unable to save mapping.", true);
    showToast("Unable to save mapping.", {error: true});
  } finally {
    if (form.isConnected) {
      artistsInput.disabled = false;
      submitButton.removeAttribute("aria-busy");
      syncAlbumArtistMappingFormState(form);
    }
  }
}

function finishAlbumArtistMappingEdit(form, albumArtist, mappedArtists) {
  const card = form.closest("[data-mapping-card]");
  if (!(card instanceof HTMLElement)) {
    return;
  }
  const source = card.querySelector(".mapping-card-source");
  const artists = card.querySelector(".mapping-card-artists");
  if (!(source instanceof HTMLElement) || !(artists instanceof HTMLElement)) {
    return;
  }
  if ((source.textContent || "").trim() !== albumArtist) {
    return;
  }
  artists.textContent = mappedArtists;
  restoreAlbumArtistMappingCard(card);
}

async function deleteRoot(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const card = button.closest("[data-root-card]");
  const position = Number(button.dataset.rootPosition || (card instanceof HTMLElement ? card.dataset.rootPosition : ""));
  if (!Number.isInteger(position)) {
    return;
  }

  const path = card instanceof HTMLElement
    ? card.querySelector(".root-card-path")?.textContent?.trim() || `Root ${position + 1}`
    : `Root ${position + 1}`;
  if (!window.confirm(`Delete ${path}?\n\nThis removes only library data for this root.`)) {
    return;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/roots/${position}/delete`, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to delete root.";
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Delete queued.";
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    showToast("Unable to delete root.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function deleteMusicBrainzOverride(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const row = button.closest("[data-musicbrainz-override-row]");
  const albumId = button.dataset.albumId || (row instanceof HTMLElement ? row.dataset.albumId : "");
  const deleteUrl = button.dataset.deleteUrl;
  if (!albumId || !deleteUrl) {
    return;
  }

  if (!window.confirm(`Delete stale MusicBrainz override for ${albumId}?`)) {
    return;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(deleteUrl, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to delete MusicBrainz override.";
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "MusicBrainz override deleted.";
    showToast(message);
    await navigate(window.location.href, {replace: true, scroll: false});
  } catch {
    showToast("Unable to delete MusicBrainz override.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function rescanLibrary(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/roots/rescan", {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to rescan library.";
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Rescan queued.";
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    showToast("Unable to rescan library.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

function setStatusMessage(formOrElement, selector, message, isError = false) {
  const element = formOrElement instanceof HTMLElement
    ? formOrElement.querySelector(selector)
    : view.querySelector(selector);
  if (!(element instanceof HTMLElement)) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function setAlbumMusicBrainzStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-musicbrainz-status]", message, isError);
}

function setAlbumTagStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-tag-status]", message, isError);
}

function setAlbumArtistMappingStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-artist-mapping-status]", message, isError);
}

function setRootFormStatus(element, message, isError = false) {
  if (!(element instanceof HTMLElement)) {
    return;
  }
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function showToast(message, options = {}) {
  if (!(toast instanceof HTMLElement) || typeof message !== "string" || !message.trim()) {
    return;
  }
  const toastMessage = document.createElement("div");
  toastMessage.className = "toast-message";
  toastMessage.classList.toggle("error", options.error === true);

  const copy = document.createElement("div");
  copy.className = "toast-copy";
  copy.textContent = message;

  const children = [copy];
  const link = createToastLink(options.link);
  if (link) {
    children.push(link);
  }

  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.dataset.closeToast = "";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "x";
  children.push(close);

  toastMessage.replaceChildren(...children);
  toast.prepend(toastMessage);
  toast.hidden = false;
  if (options.persistent === true) {
    return;
  }

  const timeout = window.setTimeout(() => {
    removeToastMessage(toastMessage);
  }, link ? linkedToastHideDelayMs : toastHideDelayMs);
  toastTimeouts.set(toastMessage, timeout);
}

function closeToast(button) {
  const toastMessage = button.closest(".toast-message");
  if (toastMessage instanceof HTMLElement) {
    removeToastMessage(toastMessage);
  }
}

function removeToastMessage(toastMessage) {
  const timeout = toastTimeouts.get(toastMessage);
  if (timeout) {
    clearTimeout(timeout);
    toastTimeouts.delete(toastMessage);
  }
  toastMessage.remove();
  if (toast instanceof HTMLElement && !toast.children.length) {
    toast.hidden = true;
  }
}

function createToastLink(link) {
  if (!link || typeof link !== "object") {
    return null;
  }
  const href = typeof link.href === "string" ? link.href.trim() : "";
  const label = typeof link.label === "string" ? link.label.trim() : "";
  if (!href || !label) {
    return null;
  }

  const anchor = document.createElement("a");
  anchor.className = "toast-link";
  anchor.href = href;
  anchor.dataset.nav = "";
  anchor.textContent = label;
  return anchor;
}

function showJobToast(job) {
  if (!(jobToasts instanceof HTMLElement) || !job || typeof job !== "object") {
    return;
  }
  const jobId = Number(job.job_id);
  if (!Number.isInteger(jobId) || jobId <= 0) {
    return;
  }
  const status = typeof job.status === "string" ? job.status : "";
  const message = typeof job.message === "string" ? job.message.trim() : "";
  if (!message) {
    return;
  }
  const toastElement = jobToastElement(jobId);
  toastElement.className = `job-toast ${status}`;
  toastElement.dataset.jobToastId = String(jobId);
  toastElement.replaceChildren(...jobToastChildren(job));
  jobToasts.prepend(toastElement);
  updateVisibleJobCard(job);
}

function jobToastElement(jobId) {
  const existing = jobToasts.querySelector(`[data-job-toast-id="${jobId}"]`);
  if (existing instanceof HTMLElement) {
    return existing;
  }
  const element = document.createElement("div");
  element.className = "job-toast";
  return element;
}

function jobToastChildren(job) {
  const status = typeof job.status === "string" ? job.status : "";
  const statusLabel = typeof job.status_label === "string" ? job.status_label : status;
  const kindLabel = typeof job.kind_label === "string" ? job.kind_label : "Job";
  const message = typeof job.message === "string" ? job.message : "";
  const reason = typeof job.reason === "string" ? job.reason.trim() : "";

  const top = document.createElement("div");
  top.className = "job-toast-top";
  const badges = document.createElement("div");
  badges.className = "job-toast-badges";
  badges.append(jobBadge("job-status", status, statusLabel));
  badges.append(jobBadge("job-kind", "", kindLabel));
  top.append(badges);

  const copy = document.createElement("div");
  copy.className = "job-toast-message";
  copy.textContent = message;

  const children = [top, copy];
  if (reason && (status === "failed" || status === "canceled")) {
    const reasonElement = document.createElement("div");
    reasonElement.className = "job-toast-reason";
    reasonElement.textContent = reason;
    children.push(reasonElement);
  }

  const actions = document.createElement("div");
  actions.className = "job-toast-actions";
  if (status === "queued" || status === "running") {
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.dataset.cancelJob = "";
    cancelButton.dataset.jobId = String(job.job_id);
    cancelButton.textContent = job.cancel_requested_at ? "Canceling..." : "Cancel";
    cancelButton.disabled = Boolean(job.cancel_requested_at);
    actions.append(cancelButton);
  } else if (status === "succeeded") {
    const refresh = document.createElement("a");
    refresh.className = "toast-link";
    refresh.href = window.location.href;
    refresh.dataset.nav = "";
    refresh.textContent = "Refresh Page";
    actions.append(refresh);
    actions.append(closeJobToastButton(job.job_id));
  } else if (status === "failed" || status === "canceled") {
    actions.append(closeJobToastButton(job.job_id));
  }
  if (actions.childNodes.length) {
    children.push(actions);
  }
  return children;
}

function jobBadge(className, status, label) {
  const badge = document.createElement("span");
  badge.className = status ? `${className} ${status}` : className;
  badge.textContent = label;
  return badge;
}

function closeJobToastButton(jobId) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.closeJobToast = "";
  button.dataset.jobId = String(jobId);
  button.textContent = "Close";
  return button;
}

function closeJobToast(button) {
  const jobId = Number(button.dataset.jobId || "");
  const toastElement = Number.isInteger(jobId)
    ? jobToasts?.querySelector(`[data-job-toast-id="${jobId}"]`)
    : button.closest("[data-job-toast-id]");
  if (toastElement instanceof HTMLElement) {
    toastElement.remove();
  }
}

async function cancelJob(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const jobId = Number(button.dataset.jobId || "");
  if (!Number.isInteger(jobId) || jobId <= 0) {
    return;
  }
  button.disabled = true;
  button.textContent = "Canceling...";
  try {
    const response = await fetch(`/api/jobs/${jobId}/cancel`, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to cancel job.";
      showToast(message, {error: true});
      button.disabled = false;
      button.textContent = "Cancel";
      return;
    }
    if (payload && payload.job) {
      showJobToast(payload.job);
    }
  } catch {
    showToast("Unable to cancel job.", {error: true});
    button.disabled = false;
    button.textContent = "Cancel";
  }
}

function updateVisibleJobCard(job) {
  const jobId = Number(job.job_id);
  if (!Number.isInteger(jobId)) {
    return;
  }
  const card = view.querySelector(`[data-job-id="${jobId}"]`);
  if (!(card instanceof HTMLElement)) {
    return;
  }
  const status = typeof job.status === "string" ? job.status : "";
  const statusLabel = typeof job.status_label === "string" ? job.status_label : status;
  const statusElement = card.querySelector(".job-status");
  if (statusElement instanceof HTMLElement) {
    statusElement.className = `job-status ${status}`;
    statusElement.textContent = statusLabel;
  }
  const messageElement = card.querySelector(".job-message");
  if (messageElement instanceof HTMLElement && typeof job.message === "string") {
    messageElement.textContent = job.message;
  }
  let reasonElement = card.querySelector(".job-reason");
  const reason = typeof job.reason === "string" ? job.reason.trim() : "";
  if (reason && (status === "failed" || status === "canceled")) {
    if (!(reasonElement instanceof HTMLElement)) {
      reasonElement = document.createElement("div");
      reasonElement.className = "job-reason";
      messageElement?.after(reasonElement);
    }
    reasonElement.textContent = reason;
  } else if (reasonElement instanceof HTMLElement) {
    reasonElement.remove();
  }
  if (status !== "queued" && status !== "running") {
    card.querySelector(".job-card-actions")?.remove();
  }
}

function rescanSettingsLinkForMessage(message) {
  if (typeof message !== "string") {
    return null;
  }
  const normalizedMessage = message.toLowerCase();
  if (!normalizedMessage.includes("rescan the affected root")
    && !normalizedMessage.includes("rescan affected roots")
    && !normalizedMessage.includes("rescan the library")) {
    return null;
  }
  return {
    href: "/roots",
    label: "Open Roots"
  };
}

async function playAlbumFromGrid(button) {
  try {
    const tracks = await tracksForAlbumButton(button);
    if (!tracks.length) {
      showToast("No tracks found for this album.", {error: true});
      return;
    }
    await playQueue(tracks.map((track) => track.trackId), 0);
  } catch (error) {
    showToast(albumPlaybackErrorMessage(error), {error: true});
  }
}

async function queueAlbumFromGrid(button) {
  try {
    const tracks = await tracksForAlbumButton(button);
    if (!tracks.length) {
      showToast("No tracks found for this album.", {error: true});
      return;
    }
    await appendTracksToQueue(tracks.map((track) => track.trackId));
  } catch (error) {
    showToast(albumPlaybackErrorMessage(error), {error: true});
  }
}

function albumPlaybackErrorMessage(error) {
  return error instanceof Error && error.message && error.message.trim()
    ? error.message
    : "Unable to load album tracks.";
}

async function tracksForAlbumButton(button) {
  const playbackUrl = albumPlaybackUrl(button);
  if (!playbackUrl) {
    throw new Error("Unable to load album tracks.");
  }
  let request = albumPlaybackCache.get(playbackUrl);
  if (!request) {
    request = fetchAlbumTracks(playbackUrl);
    albumPlaybackCache.set(playbackUrl, request);
  }
  try {
    const tracks = await request;
    cacheTracks(tracks);
    return tracks;
  } catch (error) {
    albumPlaybackCache.delete(playbackUrl);
    throw error;
  }
}

function albumPlaybackUrl(button) {
  const playbackSource = button.closest("[data-album-playback-source]");
  if (!(playbackSource instanceof HTMLElement)) {
    return "";
  }
  const playlistId = playbackSource.dataset.playlistId ? playbackSource.dataset.playlistId.trim() : "";
  if (playlistId) {
    return new URL(`/api/playlists/${encodeURIComponent(playlistId)}/playback`, window.location.origin).toString();
  }
  const albumId = playbackSource.dataset.albumId ? playbackSource.dataset.albumId.trim() : "";
  if (!albumId) {
    return "";
  }
  const url = new URL(
    `/api/albums/${encodeURIComponent(albumId).replace(/%3A/gi, ":")}/playback`,
    window.location.origin
  );
  for (const rootValue of selectedRootValuesForPlayback()) {
    url.searchParams.append("root", rootValue);
  }
  return url.toString();
}

function selectedRootValuesForPlayback() {
  const form = view.querySelector("form[data-filter-form]");
  if (form instanceof HTMLFormElement) {
    return Array.from(form.querySelectorAll('input[name="root"]:checked'))
      .map((input) => input.value.trim())
      .filter(Boolean);
  }
  const url = new URL(window.location.href);
  return url.searchParams.getAll("root").map((value) => value.trim()).filter(Boolean);
}

async function fetchAlbumTracks(playbackUrl) {
  const response = await fetch(playbackUrl, {
    headers: {Accept: "application/json"}
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload && typeof payload.error === "string" && payload.error.trim()
      ? payload.error
      : "Unable to load album tracks.";
    throw new Error(message);
  }
  if (!Array.isArray(payload)) {
    throw new Error("Unable to load album tracks.");
  }
  return payload.map(normalizeTrackPayload).filter(Boolean);
}

function normalizeTrackPayload(payload) {
  const trackId = Number(payload && payload.trackId);
  if (!Number.isFinite(trackId)) {
    return null;
  }
  return {
    trackId,
    albumId: typeof payload.albumId === "string" ? payload.albumId : "",
    audioUrl: typeof payload.audioUrl === "string" && payload.audioUrl
      ? payload.audioUrl
      : `/audio/${trackId}`,
    artUrl: typeof payload.artUrl === "string" && payload.artUrl
      ? payload.artUrl
      : `/art/32/${trackId}`,
    title: typeof payload.title === "string" && payload.title
      ? payload.title
      : `Track ${trackId}`,
    albumArtist: typeof payload.albumArtist === "string" ? payload.albumArtist : "",
    albumArtists: normalizeAlbumArtists(payload.albumArtists, payload.albumArtist),
    album: typeof payload.album === "string" ? payload.album : "",
    durationSeconds: Number.isFinite(Number(payload.durationSeconds))
      ? Number(payload.durationSeconds)
      : null,
    fileType: typeof payload.fileType === "string" ? payload.fileType : "",
    audioMimeType: typeof payload.audioMimeType === "string" ? payload.audioMimeType : "",
    audioCodec: typeof payload.audioCodec === "string" ? payload.audioCodec : "",
    unsupported: typeof payload.unsupported === "string" ? payload.unsupported : ""
  };
}

function normalizeAlbumArtists(value, fallback) {
  const artists = [];
  const seen = new Set();
  const values = Array.isArray(value) ? value : [];
  for (const item of values) {
    const artist = String(item || "").trim();
    const key = artist.toLocaleLowerCase();
    if (!artist || seen.has(key)) {
      continue;
    }
    seen.add(key);
    artists.push(artist);
  }
  const fallbackArtist = String(fallback || "").trim();
  const fallbackKey = fallbackArtist.toLocaleLowerCase();
  if (!artists.length && fallbackArtist && !seen.has(fallbackKey)) {
    artists.push(fallbackArtist);
  }
  return artists;
}

function albumArtistsFromRow(row) {
  try {
    return normalizeAlbumArtists(
      JSON.parse(row.dataset.albumArtists || "[]"),
      row.dataset.albumArtist || ""
    );
  } catch {
    return normalizeAlbumArtists([], row.dataset.albumArtist || "");
  }
}

function cacheTracks(tracks) {
  for (const track of tracks) {
    trackCache.set(track.trackId, track);
  }
}

function isTextInputTarget(target) {
  if (!(target instanceof Element)) {
    return false;
  }
  return Boolean(target.closest("input, textarea, select, audio, [contenteditable='true']"));
}

function hydrateVisibleTracks() {
  document.querySelectorAll("tr[data-track-id]").forEach((row) => {
    const track = trackFromRow(row);
    if (track) {
      trackCache.set(track.trackId, track);
    }
  });
}

function trackFromRow(row) {
  const trackId = Number(row.dataset.trackId);
  if (!Number.isFinite(trackId)) {
    return null;
  }
  return {
    trackId,
    albumId: row.dataset.albumId || "",
    audioUrl: row.dataset.audioUrl || `/audio/${trackId}`,
    artUrl: row.dataset.artUrl || `/art/32/${trackId}`,
    title: row.dataset.title || `Track ${trackId}`,
    albumArtist: row.dataset.albumArtist || "",
    albumArtists: albumArtistsFromRow(row),
    album: row.dataset.album || "",
    durationSeconds: Number.isFinite(Number(row.dataset.durationSeconds))
      ? Number(row.dataset.durationSeconds)
      : null,
    fileType: row.dataset.fileType || "",
    audioMimeType: row.dataset.audioMimeType || "",
    audioCodec: row.dataset.audioCodec || "",
    unsupported: row.dataset.unsupported || ""
  };
}

function trackById(trackId) {
  const resolvedId = Number(trackId);
  if (!Number.isFinite(resolvedId)) {
    return null;
  }
  return trackCache.get(resolvedId) || {
    trackId: resolvedId,
    albumId: "",
    audioUrl: `/audio/${resolvedId}`,
    artUrl: `/art/32/${resolvedId}`,
    title: `Track ${resolvedId}`,
    albumArtist: "",
    albumArtists: [],
    album: "",
    durationSeconds: null,
    fileType: "",
    audioMimeType: "",
    audioCodec: "",
    unsupported: ""
  };
}

function playFromRow(row) {
  hydrateVisibleTracks();
  const allRows = Array.from(view.querySelectorAll("tr[data-track-id]"));
  const rowIndex = allRows.indexOf(row);
  if (rowIndex === -1) {
    return;
  }
  if (row.dataset.queuePosition !== undefined) {
    const ids = allRows.map((candidate) => Number(candidate.dataset.trackId)).filter(Number.isFinite);
    playQueue(ids, Number(row.dataset.queuePosition) || 0, {preserveErrors: true});
    return;
  }
  const ids = allRows.slice(rowIndex).map((candidate) => Number(candidate.dataset.trackId)).filter(Number.isFinite);
  playQueue(ids, 0);
}

async function playQueue(trackIds, position, options = {}) {
  if (!trackIds.length) {
    return;
  }
  queueState = normalizeQueueState({
    track_ids: trackIds,
    position,
    loaded_track_id: trackIds[position],
    paused: false,
    errored_track_ids: options.preserveErrors ? queueState.errored_track_ids : []
  });
  void postQueue(queueState);
  playQueuePosition(position);
}

function playQueuePosition(position) {
  if (position < 0 || position >= queueState.track_ids.length) {
    return;
  }
  queueState.position = position;
  const trackId = queueState.track_ids[position];
  queueState.loaded_track_id = trackId;
  playTrack(trackById(trackId), {restart: true});
}

async function playTrack(track, options = {}) {
  if (!track) {
    return;
  }
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  const unsupported = unsupportedPlaybackMessage(track);
  if (unsupported) {
    handlePlaybackFailure(track, unsupported);
    return;
  }
  queueState.loaded_track_id = track.trackId;
  queueState.paused = false;
  if (audio.getAttribute("src") !== track.audioUrl) {
    audio.src = track.audioUrl;
  }
  if (options.restart) {
    try {
      audio.currentTime = 0;
    } catch {
      // Some media backends reject seeking before metadata is ready.
    }
  }
  updateNowPlaying(track);
  postPlayback({
    loaded_track_id: track.trackId,
    position: queueState.position,
    paused: false,
    errored_track_ids: queueState.errored_track_ids
  });
  try {
    await audio.play();
  } catch (err) {
    if (trackHasPlaybackError(track.trackId) && loadedTrackId() !== track.trackId) {
      return;
    }
    handlePlaybackFailure(track, playbackErrorMessage(track, err));
  }
  updatePlaybackUi();
}

function handlePlaybackFailure(track, message) {
  if (!track) {
    return;
  }
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  const trackId = Number(track.trackId);
  if (!Number.isFinite(trackId)) {
    return;
  }

  const wasAlreadyErrored = trackHasPlaybackError(trackId);
  addPlaybackError(trackId);
  const toastMessage = playbackFailureToastMessage(track, message);
  if (!wasAlreadyErrored) {
    showToast(toastMessage, {error: true, persistent: true});
  }

  const failedPosition = failureQueuePosition(trackId);
  const nextPosition = nextPlayableQueuePosition(failedPosition);
  if (nextPosition !== -1) {
    playQueuePosition(nextPosition);
    return;
  }

  queueState.position = failedPosition === -1
    ? queueState.track_ids.length
    : Math.min(failedPosition + 1, queueState.track_ids.length);
  queueState.loaded_track_id = null;
  queueState.paused = true;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  updateNowPlaying(null);
  postPlayback({
    position: queueState.position,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: queueState.errored_track_ids
  });
  updatePlaybackUi();
}

function playbackFailureToastMessage(track, message) {
  const detail = typeof message === "string" && message.trim()
    ? message.trim()
    : "Playback failed.";
  const title = track && typeof track.title === "string" ? track.title.trim() : "";
  return title ? `Could not play "${title}". ${detail}` : detail;
}

function addPlaybackError(trackId) {
  if (!Number.isFinite(Number(trackId))) {
    return;
  }
  const resolvedTrackId = Number(trackId);
  if (!trackHasPlaybackError(resolvedTrackId)) {
    queueState.errored_track_ids = [...queueState.errored_track_ids, resolvedTrackId];
  }
}

function trackHasPlaybackError(trackId) {
  const resolvedTrackId = Number(trackId);
  return Number.isFinite(resolvedTrackId)
    && queueState.errored_track_ids.includes(resolvedTrackId);
}

function failureQueuePosition(trackId) {
  const currentPosition = queueLoadedPosition();
  if (
    currentPosition !== -1
    && queueState.track_ids[currentPosition] === trackId
  ) {
    return currentPosition;
  }
  if (
    queueState.position >= 0
    && queueState.position < queueState.track_ids.length
    && queueState.track_ids[queueState.position] === trackId
  ) {
    return queueState.position;
  }
  return queueState.track_ids.indexOf(trackId);
}

function nextPlayableQueuePosition(position) {
  for (let index = position + 1; index < queueState.track_ids.length; index += 1) {
    if (!trackHasPlaybackError(queueState.track_ids[index])) {
      return index;
    }
  }
  return -1;
}

function previousPlayableQueuePosition(position) {
  for (let index = position - 1; index >= 0; index -= 1) {
    if (!trackHasPlaybackError(queueState.track_ids[index])) {
      return index;
    }
  }
  return -1;
}

function moveQueue(delta) {
  if (!queueState.track_ids.length) {
    return;
  }
  const currentPosition = queuePositionForControls();
  const nextPosition = delta > 0
    ? nextPlayableQueuePosition(currentPosition)
    : previousPlayableQueuePosition(currentPosition);
  if (nextPosition === -1) {
    return;
  }
  playQueuePosition(nextPosition);
}

function queueLoadedPosition() {
  const loadedId = loadedTrackId();
  if (loadedId === null) {
    return -1;
  }
  if (
    queueState.position >= 0
    && queueState.position < queueState.track_ids.length
    && queueState.track_ids[queueState.position] === loadedId
  ) {
    return queueState.position;
  }
  return queueState.track_ids.indexOf(loadedId);
}

function queuePositionForControls() {
  const loadedPosition = queueLoadedPosition();
  if (loadedPosition !== -1) {
    return loadedPosition;
  }
  if (queueState.position >= 0 && queueState.position < queueState.track_ids.length) {
    return queueState.position;
  }
  if (queueState.track_ids.length) {
    return queueState.track_ids.length - 1;
  }
  return 0;
}

function loadedTrackId() {
  return queueState.loaded_track_id === null || !Number.isFinite(Number(queueState.loaded_track_id))
    ? null
    : Number(queueState.loaded_track_id);
}

function updateNowPlaying(track) {
  if (!track || !(nowPlaying instanceof HTMLElement)) {
    nowPlaying.textContent = "";
    return;
  }
  nowPlaying.replaceChildren();

  const cover = document.createElement("img");
  cover.className = "cover now-playing-cover";
  cover.src = track.artUrl;
  cover.alt = "";
  cover.loading = "eager";

  const copy = document.createElement("div");
  copy.className = "now-playing-copy";

  const title = document.createElement("div");
  title.className = "now-playing-title";
  title.textContent = track.title;
  copy.append(title);

  if (!track.albumArtist && !track.album) {
    nowPlaying.replaceChildren(cover, copy);
    return;
  }

  const meta = document.createElement("div");
  meta.className = "now-playing-meta";
  const artistAdded = appendNowPlayingArtistLabels(meta, track);
  const albumAdded = appendNowPlayingLabel(
    meta,
    track.album,
    albumDetailUrl(track),
    "now-playing-link now-playing-album"
  );
  if (!artistAdded && !albumAdded) {
    return;
  }
  if (artistAdded && albumAdded) {
    const separator = document.createElement("span");
    separator.className = "now-playing-separator";
    separator.textContent = "•";
    meta.insertBefore(separator, meta.lastChild);
  }
  copy.append(meta);
  nowPlaying.replaceChildren(cover, copy);
}

function appendNowPlayingArtistLabels(container, track) {
  const artists = normalizeAlbumArtists(
    track && track.albumArtists,
    track && track.albumArtist
  );
  let added = false;
  for (const artist of artists) {
    if (added) {
      const separator = document.createElement("span");
      separator.className = "now-playing-artist-separator";
      separator.textContent = ", ";
      container.append(separator);
    }
    const artistAdded = appendNowPlayingLabel(
      container,
      artist,
      albumArtistFilterUrl(artist),
      "now-playing-link now-playing-artist"
    );
    added = added || artistAdded;
  }
  return added;
}

function appendNowPlayingLabel(container, label, href, className) {
  const text = String(label || "").trim();
  if (!text) {
    return false;
  }
  if (href) {
    const link = document.createElement("a");
    link.href = href;
    link.dataset.nav = "";
    link.className = className;
    link.textContent = text;
    container.append(link);
    return true;
  }
  const span = document.createElement("span");
  span.textContent = text;
  container.append(span);
  return true;
}

function albumArtistFilterUrl(artist) {
  artist = String(artist || "").trim();
  if (!artist || artist === "<unknown artist>") {
    return "";
  }
  const url = new URL("/", window.location.origin);
  url.searchParams.append("artist", artist);
  return url.toString();
}

function albumDetailUrl(track) {
  const albumId = String(track && track.albumId || "").trim();
  if (!albumId) {
    return "";
  }
  if (albumId.startsWith("playlist:")) {
    const playlistId = albumId.slice("playlist:".length).trim();
    if (!playlistId) {
      return "";
    }
    return new URL(
      `/playlists/${encodeURIComponent(playlistId)}`,
      window.location.origin
    ).toString();
  }
  return new URL(
    `/albums/${encodeURIComponent(albumId).replace(/%3A/gi, ":")}`,
    window.location.origin
  ).toString();
}

function updatePlaybackUi() {
  const loadedId = loadedTrackId();
  const currentQueuePosition = queueLoadedPosition();
  const playing = playbackIsActive();
  const hasPlayableQueuedTrack = queueState.track_ids.some((trackId) => !trackHasPlaybackError(trackId));
  playButton.textContent = playing ? "Pause" : "Play";
  playButton.setAttribute("aria-pressed", String(playing));
  playButton.disabled = loadedId === null && !hasPlayableQueuedTrack;
  previousButton.disabled = !canMove(-1);
  nextButton.disabled = !canMove(1);
  queueLink.classList.toggle("active", document.body.dataset.page === "queue");
  document.querySelectorAll("[data-queue-append]").forEach((button) => {
    if (button instanceof HTMLButtonElement) {
      button.disabled = !queueState.track_ids.length;
    }
  });

  const loadedTrack = loadedId === null ? null : trackById(loadedId);
  if (loadedTrack && !nowPlaying.textContent) {
    updateNowPlaying(loadedTrack);
  }
  updateQueuePageMeta();

  document.querySelectorAll("tr[data-track-id]").forEach((row) => {
    const trackId = Number(row.dataset.trackId);
    if (row.dataset.queuePosition !== undefined) {
      const position = Number(row.dataset.queuePosition);
      const rowCurrent = (
        position === currentQueuePosition
        && position === queueState.position
        && trackId === loadedId
      );
      row.classList.toggle("current", rowCurrent);
      row.classList.toggle("playing", rowCurrent && playing);
      row.classList.toggle("queue-played", position < queueState.position);
      row.classList.toggle("queue-active", position === queueState.position);
      row.classList.toggle("queue-error", trackHasPlaybackError(trackId));
      const status = row.querySelector(".queue-status-label");
      if (status) {
        status.textContent = queueStatus(trackId, position);
      }
      return;
    }
    const rowCurrent = trackId === loadedId;
    row.classList.toggle("current", rowCurrent);
    row.classList.toggle("playing", rowCurrent && playing);
  });
}

function totalDurationText(tracks) {
  const totalSeconds = tracks.reduce((sum, track) => {
    const seconds = Number(track && track.durationSeconds);
    return sum + (Number.isFinite(seconds) ? seconds : 0);
  }, 0);
  const totalMinutes = Math.round(totalSeconds / 60);
  if (totalMinutes <= 0) {
    return "";
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  const parts = [];
  if (hours) {
    parts.push(`${hours} ${hours === 1 ? "hour" : "hours"}`);
  }
  if (minutes) {
    parts.push(`${minutes} ${minutes === 1 ? "minute" : "minutes"}`);
  }
  return parts.join(", ");
}

function updateQueuePageMeta() {
  const meta = view.querySelector("[data-queue-meta]");
  if (!(meta instanceof HTMLElement)) {
    return;
  }
  const trackCount = queueState.track_ids.length;
  const playedCount = Math.min(queueState.position, trackCount);
  const parts = [
    `${trackCount} ${trackCount === 1 ? "track" : "tracks"}`,
    `${playedCount} played`,
  ];
  const durationText = totalDurationText(queueState.track_ids.map((trackId) => trackById(trackId)));
  if (durationText) {
    parts.push(durationText);
  }
  meta.dataset.durationText = durationText;
  meta.textContent = parts.join(" - ");
}

function canMove(delta) {
  if (!queueState.track_ids.length) {
    return false;
  }
  const currentPosition = queuePositionForControls();
  const nextPosition = delta > 0
    ? nextPlayableQueuePosition(currentPosition)
    : previousPlayableQueuePosition(currentPosition);
  return nextPosition !== -1;
}

function queueStatus(trackId, position) {
  if (trackHasPlaybackError(trackId)) {
    return "Error";
  }
  if (position < queueState.position) {
    return "Played";
  }
  if (position === queueState.position) {
    if (loadedTrackId() === trackId && playbackIsActive()) {
      return "Now";
    }
    if (loadedTrackId() === trackId) {
      return "Paused";
    }
  }
  return "Next";
}

async function postQueue(state) {
  try {
    const response = await fetch("/api/queue", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        track_ids: state.track_ids,
        position: state.position,
        loaded_track_id: state.loaded_track_id,
        paused: state.paused,
        errored_track_ids: state.errored_track_ids
      })
    });
    if (!response.ok) {
      throw new Error(`queue request failed: ${response.status}`);
    }
    return normalizeQueueState(await response.json());
  } catch {
    return null;
  }
}

async function appendTrackToQueue(track) {
  if (!track) {
    return;
  }
  cacheTracks([track]);
  await appendTracksToQueue([track.trackId]);
}

async function appendTracksToQueue(trackIds) {
  if (!queueState.track_ids.length || !trackIds.length) {
    updatePlaybackUi();
    return;
  }
  queueState = normalizeQueueState({
    ...queueState,
    track_ids: [...queueState.track_ids, ...trackIds]
  });
  const syncedState = await postQueue(queueState);
  if (syncedState) {
    queueState = syncedState;
  }
  updatePlaybackUi();
}

function queueRemovalState(position) {
  if (position < 0 || position >= queueState.track_ids.length) {
    return null;
  }
  const trackIds = [...queueState.track_ids];
  const currentPosition = queueLoadedPosition();
  const currentLoadedTrackId = loadedTrackId();
  trackIds.splice(position, 1);

  let nextPosition = queueState.position;
  let nextLoadedTrackId = currentLoadedTrackId;
  let nextPaused = queueState.paused;
  let playNext = false;
  let stopPlayback = false;

  if (position < nextPosition) {
    nextPosition -= 1;
  }

  if (position === currentPosition) {
    if (position < trackIds.length) {
      nextPosition = position;
      nextLoadedTrackId = trackIds[position];
      nextPaused = false;
      playNext = true;
    } else {
      nextPosition = trackIds.length;
      nextLoadedTrackId = null;
      nextPaused = true;
      stopPlayback = currentLoadedTrackId !== null;
    }
  }

  return {
    state: normalizeQueueState({
      track_ids: trackIds,
      position: nextPosition,
      loaded_track_id: nextLoadedTrackId,
      paused: nextPaused,
      errored_track_ids: queueState.errored_track_ids
    }),
    playNext,
    stopPlayback,
  };
}

function clearLoadedPlayback() {
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  updateNowPlaying(null);
}

async function refreshQueuePage() {
  if (document.body.dataset.page !== "queue") {
    return;
  }
  try {
    const html = await fetchFragment(window.location.href);
    renderFragment(html, window.location.href, {
      history: false,
      scroll: false
    });
  } catch {
    updatePlaybackUi();
  }
}

async function deleteQueueTrackFromQueue(row) {
  const position = Number(row.dataset.queuePosition);
  if (!Number.isFinite(position)) {
    return;
  }
  const removal = queueRemovalState(position);
  if (!removal) {
    return;
  }

  queueState = removal.state;
  const syncedState = await postQueue(queueState);
  if (syncedState) {
    queueState = syncedState;
  }

  if (removal.playNext) {
    playQueuePosition(queueState.position);
  } else if (removal.stopPlayback) {
    clearLoadedPlayback();
    updatePlaybackUi();
  } else {
    updatePlaybackUi();
  }

  await refreshQueuePage();
}

async function postPlayback(payload) {
  try {
    await fetch("/api/playback", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
  } catch {
    return;
  }
}

function unsupportedPlaybackMessage(track, afterPlaybackError = false) {
  if (!track) {
    return "";
  }
  if (track.unsupported) {
    return track.unsupported;
  }
  const browser = browserName();
  const fileType = String(track.fileType || "").toLowerCase();
  const codec = String(track.audioCodec || "").toLowerCase();
  const format = audioFormatDescription(track);

  if (fileType === "m4a" && codec === "alac") {
    if (browser === "Safari") {
      return afterPlaybackError
        ? `${browser} could not play ${format}. Safari usually supports ALAC .m4a, so this file may be malformed or use an unsupported variant.`
        : "";
    }
    return `${browser} cannot play ${format}. Safari can play ALAC .m4a; for lossless playback in ${browser}, use FLAC.`;
  }

  const canPlay = browserCanPlayTrack(track);
  if (canPlay === "") {
    return `${browser} reports that it cannot play ${format}.`;
  }
  return "";
}

function playbackErrorMessage(track, err) {
  const supportMessage = unsupportedPlaybackMessage(track, true);
  if (supportMessage) {
    return supportMessage;
  }
  if (audio.error && (audio.error.code === 3 || audio.error.code === 4)) {
    return `${browserName()} could not read ${audioFormatDescription(track)}.`;
  }
  if (err && err.message) {
    return err.message;
  }
  if (audio.error && audio.error.message) {
    return audio.error.message;
  }
  return "Playback failed.";
}

function browserCanPlayTrack(track) {
  const mimeType = String(track.audioMimeType || "").toLowerCase();
  const codec = String(track.audioCodec || "").toLowerCase();
  if (mimeType && codec) {
    return audio.canPlayType(`${mimeType}; codecs="${codec}"`);
  }
  if (mimeType) {
    return audio.canPlayType(mimeType);
  }
  return null;
}

function audioFormatDescription(track) {
  if (!track) {
    return "this audio file";
  }
  const fileType = String(track.fileType || "").toLowerCase();
  const codec = String(track.audioCodec || "").toLowerCase();
  if (fileType === "m4a" && codec === "alac") {
    return "ALAC .m4a";
  }
  if (fileType === "m4a" && codec.startsWith("mp4a")) {
    return "AAC .m4a";
  }
  if (fileType) {
    return `.${fileType} file`;
  }
  return "this audio file";
}

function browserName() {
  const userAgent = navigator.userAgent || "";
  if (userAgent.includes("Edg/")) {
    return "Edge";
  }
  if (userAgent.includes("OPR/")) {
    return "Opera";
  }
  if (userAgent.includes("Firefox/")) {
    return "Firefox";
  }
  if (userAgent.includes("Chrome/") || userAgent.includes("CriOS/")) {
    return "Chrome";
  }
  if (userAgent.includes("Safari/")) {
    return "Safari";
  }
  return "This browser";
}

function replaceBrokenImage(event) {
  if (!(event.target instanceof HTMLImageElement)) {
    return;
  }
  if (!event.target.classList.contains("cover") && !event.target.classList.contains("album-cover")) {
    return;
  }
  const placeholder = document.createElement("span");
  placeholder.className = event.target.classList.contains("cover")
    ? "cover"
    : "album-cover-placeholder";
  placeholder.setAttribute("aria-hidden", "true");
  event.target.replaceWith(placeholder);
}

view.addEventListener("error", replaceBrokenImage, true);
hydrateVisibleTracks();
updatePlaybackUi();
