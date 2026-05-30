const fragmentHeader = "X-Kukicha-Fragment";
const view = document.getElementById("view");
const audio = document.getElementById("audio");
const playButton = document.getElementById("play");
const previousButton = document.getElementById("previous");
const nextButton = document.getElementById("next");
const nowPlaying = document.getElementById("now-playing");
const progressInput = document.getElementById("playback-progress");
const elapsedTime = document.getElementById("elapsed-time");
const durationTime = document.getElementById("duration-time");
const volumeInput = document.getElementById("volume");
const volumeToggle = document.getElementById("volume-toggle");
const volumeIcon = document.getElementById("volume-icon");
const toast = document.getElementById("toast");
const jobToasts = document.getElementById("job-toasts");
const confirmationDialog = document.getElementById("confirmation-dialog");
const confirmationTitle = document.querySelector("[data-confirmation-title]");
const confirmationMessage = document.querySelector("[data-confirmation-message]");
const confirmationCancel = document.querySelector("[data-confirmation-cancel]");
const confirmationConfirm = document.querySelector("[data-confirmation-confirm]");
const keyboardShortcutsDialog = document.getElementById("keyboard-shortcuts-dialog");
const keyboardShortcutsClose = document.querySelector("[data-close-keyboard-shortcuts]");
const trackCache = new Map();
const albumPlaybackCache = new Map();
const dropdownMenuSelector = "details[data-dropdown-menu]";
const toastHideDelayMs = readToastDelayMs("toastTimeoutMs", 5000);
const volumeIconPathData = [
  "M9.741.85a.75.75 0 0 1 .375.65v13a.75.75 0 0 1-1.125.65l-6.925-4a3.64 3.64 0 0 1-1.33-4.967 3.64 3.64 0 0 1 1.33-1.332l6.925-4a.75.75 0 0 1 .75 0zm-6.924 5.3a2.14 2.14 0 0 0 0 3.7l5.8 3.35V2.8zm8.683 4.29V5.56a2.75 2.75 0 0 1 0 4.88",
  "M11.5 13.614a5.752 5.752 0 0 0 0-11.228v1.55a4.252 4.252 0 0 1 0 8.127z"
];
const volumeMutedIconPathData = [
  "M13.86 5.47a.75.75 0 0 0-1.061 0l-1.47 1.47-1.47-1.47A.75.75 0 0 0 8.8 6.53L10.269 8l-1.47 1.47a.75.75 0 1 0 1.06 1.06l1.47-1.47 1.47 1.47a.75.75 0 0 0 1.06-1.06L12.39 8l1.47-1.47a.75.75 0 0 0 0-1.06",
  "M10.116 1.5A.75.75 0 0 0 8.991.85l-6.925 4a3.64 3.64 0 0 0-1.33 4.967 3.64 3.64 0 0 0 1.33 1.332l6.925 4a.75.75 0 0 0 1.125-.649v-1.906a4.7 4.7 0 0 1-1.5-.694v1.3L2.817 9.852a2.14 2.14 0 0 1-.781-2.92c.187-.324.456-.594.78-.782l5.8-3.35v1.3c.45-.313.956-.55 1.5-.694z"
];
const durationInfinityIconPathData = "M20.288 9.463a4.856 4.856 0 0 0-4.336-2.3 4.586 4.586 0 0 0-3.343 1.767c.071.116.148.226.212.347l.879 1.652.134-.254a2.71 2.71 0 0 1 2.206-1.519 2.845 2.845 0 1 1 0 5.686 2.708 2.708 0 0 1-2.205-1.518L13.131 12l-1.193-2.26a4.709 4.709 0 0 0-3.89-2.581 4.845 4.845 0 1 0 0 9.682 4.586 4.586 0 0 0 3.343-1.767c-.071-.116-.148-.226-.212-.347l-.879-1.656-.134.254a2.71 2.71 0 0 1-2.206 1.519 2.855 2.855 0 0 1-2.559-1.369 2.825 2.825 0 0 1 0-2.946 2.862 2.862 0 0 1 2.442-1.374h.121a2.708 2.708 0 0 1 2.205 1.518l.7 1.327 1.193 2.26a4.709 4.709 0 0 0 3.89 2.581h.209a4.846 4.846 0 0 0 4.127-7.378z";

let queueState = readInitialQueueState();
let appHistoryDepth = initialAppHistoryDepth();
let scrollSaveFrame = 0;
let isRestoringScroll = false;
let scrollRestoreToken = 0;
const toastTimeouts = new WeakMap();
const jobToastTimeouts = new WeakMap();
const jobStatusRanks = new Map([
  ["queued", 0],
  ["running", 1],
  ["succeeded", 2],
  ["failed", 2],
  ["canceled", 2]
]);
const jobLatestStates = new Map();
let jobsSource = null;
let jobsStreamLoadPending = false;
let suppressPauseStateUntilPlay = false;
let pendingPauseCommitTimeout = 0;
let manualPauseRequested = false;
let activePlaylistMenu = null;
let activePlaylistOptions = null;
let activePlaylistSourceOptions = null;
let pageIsUnloading = false;
let keyboardShortcutsReturnFocus = null;
let confirmationReturnFocus = null;
let confirmationResolve = null;
let rescanLibraryPending = false;
const submittedIndeterminatePlayKeys = new Set();
let activePlaybackEngine = null;
let pendingEngineStartKey = "";
const audioBufferLoads = new Map();
const webAudioSilenceTrimThreshold = 0.0005;
const webAudioMaxSilenceTrimSeconds = 0.15;
const webAudioMinSilenceTrimSeconds = 0.003;
const webAudioMinimumSourceDurationSeconds = 0.001;

class NativeAudioEngine {
  constructor(audioElement) {
    this.audio = audioElement;
  }

  async playTrack(track, options = {}) {
    if (this.audio.getAttribute("src") !== track.audioUrl) {
      this.audio.src = track.audioUrl;
    }
    if (options.restart) {
      try {
        this.audio.currentTime = 0;
      } catch {
        // Some media backends reject seeking before metadata is ready.
      }
    }
    await this.audio.play();
  }

  pause() {
    this.audio.pause();
  }

  clear() {
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
  }

  currentTimeSeconds() {
    return clampNumber(Number(this.audio.currentTime), 0, Number.MAX_SAFE_INTEGER);
  }

  durationSeconds() {
    const duration = Number(this.audio.duration);
    return Number.isFinite(duration) && duration > 0 ? duration : null;
  }

  seek(seconds) {
    this.audio.currentTime = seconds;
  }

  isPaused() {
    return this.audio.paused;
  }

  isSeeking() {
    return Boolean(this.audio.seeking);
  }

  setVolume() {}
}

class WebAudioPlaybackEngine {
  constructor() {
    this.context = null;
    this.gainNode = null;
    this.segment = [];
    this.currentEntry = null;
    this.activeSource = null;
    this.nextSource = null;
    this.htmlAudioActive = false;
    this.transitionTimer = 0;
    this.progressFrame = 0;
    this.startedAt = 0;
    this.startOffsetSeconds = 0;
    this.positionMs = 0;
    this.paused = true;
    this.suppressCallbacks = false;
    this.waitingForNext = false;
    this.loadToken = 0;
  }

  canPlayTrack(track) {
    return webAudioCanPlayTrack(track);
  }

  async playTrack(track, options = {}) {
    const startPosition = Number.isInteger(options.position)
      ? options.position
      : queuePositionForTrack(track.trackId);
    const context = this.ensureContext();
    await resumeAudioContext(context);
    if (
      !options.restart
      && this.currentEntry
      && this.currentEntry.track.trackId === track.trackId
    ) {
      if (this.paused && this.currentEntry.buffer) {
        this.startEntry(this.currentEntry, this.currentTimeSeconds());
      } else if (this.paused) {
        await this.startHtmlEntry(this.currentEntry, this.currentTimeSeconds(), {
          loadToken: this.loadToken
        });
      } else {
        this.prefetchNextTrack();
        this.scheduleNextTrack();
      }
      return;
    }

    this.loadSegment(startPosition);
    const loadToken = this.loadToken;
    const entry = this.entryForPosition(startPosition);
    if (!entry) {
      throw new Error("Web Audio playback is unavailable for this track.");
    }
    void this.loadEntryBuffer(entry, loadToken)
      .then(() => {
        if (
          loadToken === this.loadToken
          && !this.paused
          && this.currentEntry === entry
          && this.htmlAudioActive
        ) {
          this.switchHtmlToWebAudio(entry);
        }
      })
      .catch(() => {});
    await this.startHtmlEntry(entry, 0, {loadToken});
  }

  pause() {
    if (this.paused) {
      return;
    }
    this.positionMs = this.currentTimeSeconds() * 1000;
    this.paused = true;
    this.stopScheduledPlayback();
    if (!this.suppressCallbacks) {
      handleEnginePaused();
    }
  }

  clear(options = {}) {
    this.loadToken += 1;
    const previousSuppression = this.suppressCallbacks;
    this.suppressCallbacks = Boolean(options.suppressCallbacks);
    try {
      this.stopScheduledPlayback({clearHtmlSource: true});
    } finally {
      this.segment = [];
      this.currentEntry = null;
      this.positionMs = 0;
      this.paused = true;
      this.htmlAudioActive = false;
      this.waitingForNext = false;
      this.suppressCallbacks = previousSuppression;
    }
  }

  currentTimeSeconds() {
    if (this.htmlAudioActive && !this.paused) {
      return clampNumber(Number(audio.currentTime), 0, this.playableDuration(this.currentEntry));
    }
    if (!this.paused && this.context && this.currentEntry && this.currentEntry.buffer) {
      const elapsed = this.context.currentTime - this.startedAt;
      return clampNumber(
        this.startOffsetSeconds + elapsed,
        0,
        this.playableDuration(this.currentEntry)
      );
    }
    return clampNumber(this.positionMs / 1000, 0, Number.MAX_SAFE_INTEGER);
  }

  durationSeconds() {
    if (!this.currentEntry) {
      return null;
    }
    return trackDurationSeconds(this.currentEntry.track);
  }

  seek(seconds) {
    if (!this.currentEntry) {
      return;
    }
    const offset = this.clampEntryOffset(this.currentEntry, seconds);
    this.positionMs = offset * 1000;
    if (this.htmlAudioActive) {
      try {
        audio.currentTime = offset;
      } catch {
        // Some media backends reject seeking before metadata is ready.
      }
      if (!this.paused && this.currentEntry.buffer) {
        this.switchHtmlToWebAudio(this.currentEntry);
      } else {
        updatePlaybackProgress();
      }
      return;
    }
    if (!this.paused && this.currentEntry.buffer) {
      this.startEntry(this.currentEntry, offset, {notifyStart: false});
      return;
    }
    this.prefetchNextTrack();
    updatePlaybackProgress();
  }

  isPaused() {
    return this.paused;
  }

  isSeeking() {
    return false;
  }

  setVolume(volume) {
    if (this.gainNode) {
      this.gainNode.gain.value = volume;
    }
  }

  ensureContext() {
    if (this.context) {
      return this.context;
    }
    const AudioContextConstructor = webAudioConstructor();
    if (!AudioContextConstructor) {
      throw new Error("Web Audio playback is unavailable.");
    }
    this.context = new AudioContextConstructor();
    this.gainNode = this.context.createGain();
    this.gainNode.gain.value = effectivePlaybackVolume();
    this.gainNode.connect(this.context.destination);
    return this.context;
  }

  loadSegment(startPosition) {
    this.clear({suppressCallbacks: true});
    this.loadToken += 1;
    const segment = webAudioQueueSegment(startPosition);
    if (!segment.length) {
      return;
    }
    this.segment = segment.map((entry) => ({
      ...entry,
      sourcePath: audioSourcePath(entry.track),
      buffer: null,
      bufferPromise: null,
      trim: null
    }));
    this.currentEntry = this.segment[0];
    this.prefetchNextTrack();
    this.setVolume(effectivePlaybackVolume());
  }

  async startHtmlEntry(entry, offsetSeconds = 0, options = {}) {
    if (!entry || !entry.track) {
      return;
    }
    this.stopScheduledPlayback({clearHtmlSource: true});
    const offset = this.clampEntryOffset(entry, offsetSeconds);
    this.currentEntry = entry;
    this.positionMs = offset * 1000;
    this.paused = false;
    this.waitingForNext = false;
    this.htmlAudioActive = true;
    if (audio.getAttribute("src") !== entry.track.audioUrl) {
      audio.src = entry.track.audioUrl;
    }
    try {
      audio.currentTime = offset;
    } catch {
      // Some media backends reject seeking before metadata is ready.
    }
    this.prefetchNextTrack();
    this.startProgressLoop();
    try {
      await audio.play();
    } catch (error) {
      if (
        mediaPlaybackWasAborted(error)
        && (
          options.loadToken !== this.loadToken
          || this.currentEntry !== entry
          || !this.htmlAudioActive
        )
      ) {
        return;
      }
      throw error;
    }
  }

  async loadEntryBuffer(entry, loadToken, options = {}) {
    if (entry.buffer) {
      return entry.buffer;
    }
    if (!entry.bufferPromise) {
      const context = this.ensureContext();
      entry.bufferPromise = fetchAudioBuffer(entry.sourcePath, context)
        .then((buffer) => {
          if (loadToken === this.loadToken) {
            entry.buffer = buffer;
            entry.trim = audioBufferSilenceTrim(buffer);
          }
          return buffer;
        })
        .catch((error) => {
          if (options.reportErrors && loadToken === this.loadToken) {
            handleBufferedPlaybackError(entry, error);
          }
          throw error;
        })
        .finally(() => {
          entry.bufferPromise = null;
        });
    }
    return entry.bufferPromise;
  }

  startEntry(entry, offsetSeconds = 0, options = {}) {
    if (!entry || !entry.buffer) {
      return;
    }
    this.stopScheduledPlayback({clearHtmlSource: true});
    const context = this.ensureContext();
    const offset = this.clampEntryOffset(entry, offsetSeconds);
    this.currentEntry = entry;
    this.startedAt = context.currentTime;
    this.startOffsetSeconds = offset;
    this.positionMs = offset * 1000;
    this.paused = false;
    this.waitingForNext = false;
    this.htmlAudioActive = false;
    this.activeSource = this.createSource(entry, context.currentTime, offset);
    if (options.notifyStart !== false && !this.suppressCallbacks) {
      handleEnginePlaybackStarted(entry.track, entry.position);
    }
    this.prefetchNextTrack();
    this.scheduleNextTrack();
    this.startProgressLoop();
  }

  prefetchNextTrack() {
    if (!this.context || !this.currentEntry) {
      return;
    }
    const nextEntry = this.nextEntryAfter(this.currentEntry.position);
    if (!nextEntry) {
      return;
    }
    if (nextEntry.buffer || nextEntry.bufferPromise) {
      return;
    }
    const loadToken = this.loadToken;
    void this.loadEntryBuffer(nextEntry, loadToken, {reportErrors: true})
      .then(() => {
        if (
          loadToken === this.loadToken
          && !this.paused
          && !this.waitingForNext
          && this.currentEntry
          && this.nextEntryAfter(this.currentEntry.position) === nextEntry
        ) {
          this.scheduleNextTrack();
        }
      })
      .catch(() => {});
  }

  scheduleNextTrack() {
    this.clearTransitionTimer();
    this.stopSource(this.nextSource);
    this.nextSource = null;
    if (
      this.paused
      || this.htmlAudioActive
      || !this.context
      || !this.currentEntry
      || !this.currentEntry.buffer
    ) {
      return;
    }
    const loadToken = this.loadToken;
    const currentEntry = this.currentEntry;
    const nextEntry = this.nextEntryAfter(currentEntry.position);
    const endTime = this.startedAt + Math.max(
      0,
      this.playableDuration(currentEntry) - this.startOffsetSeconds
    );
    if (nextEntry && nextEntry.buffer) {
      const startTime = Math.max(this.context.currentTime, endTime);
      this.nextSource = this.createSource(nextEntry, startTime, 0);
      this.scheduleTransition(currentEntry, nextEntry, startTime, loadToken);
      return;
    }
    if (nextEntry) {
      void this.loadEntryBuffer(nextEntry, loadToken, {reportErrors: true})
        .then(() => {
          if (
            loadToken === this.loadToken
            && !this.paused
            && !this.waitingForNext
            && this.currentEntry === currentEntry
          ) {
            this.scheduleNextTrack();
          }
        })
        .catch(() => {});
    }
    this.scheduleTransition(currentEntry, nextEntry, endTime, loadToken);
  }

  scheduleTransition(currentEntry, nextEntry, transitionTime, loadToken) {
    this.clearTransitionTimer();
    const delayMs = Math.max(0, (transitionTime - this.context.currentTime) * 1000);
    this.transitionTimer = window.setTimeout(() => {
      this.transitionTimer = 0;
      this.finishEntryAndAdvance(currentEntry, nextEntry, loadToken);
    }, delayMs);
    if (this.transitionTimer && typeof this.transitionTimer.unref === "function") {
      this.transitionTimer.unref();
    }
  }

  finishEntryAndAdvance(finishedEntry, nextEntry, loadToken) {
    if (
      loadToken !== this.loadToken
      || this.suppressCallbacks
      || !finishedEntry
    ) {
      return;
    }
    this.markSourceHandled(this.activeSource);
    this.positionMs = this.playableDuration(finishedEntry) * 1000;
    handleEngineTrackFinished(finishedEntry.track);
    if (nextEntry && this.nextSource && this.nextSource.kukichaEntry === nextEntry) {
      this.promoteScheduledNext(nextEntry);
      return;
    }
    if (nextEntry) {
      this.activeSource = null;
      this.waitingForNext = true;
      this.stopProgressLoop();
      this.startEntryWhenReady(nextEntry, loadToken, finishedEntry.position);
      return;
    }
    this.paused = true;
    this.activeSource = null;
    this.stopProgressLoop();
    handleEngineFinishedAll();
  }

  startEntryWhenReady(entry, loadToken, finishedPosition) {
    void this.loadEntryBuffer(entry, loadToken, {reportErrors: true})
      .then(() => {
        if (loadToken === this.loadToken && entry.buffer) {
          this.startEntry(entry, 0);
        }
      })
      .catch(() => {
        if (loadToken !== this.loadToken) {
          return;
        }
        this.waitingForNext = false;
        const nextPosition = nextPlayableQueuePosition(finishedPosition);
        if (nextPosition !== -1) {
          playQueuePosition(nextPosition);
          return;
        }
        this.paused = true;
        this.stopProgressLoop();
        handleEngineFinishedAll();
      });
  }

  promoteScheduledNext(entry) {
    this.clearTransitionTimer();
    this.activeSource = this.nextSource;
    this.nextSource = null;
    this.currentEntry = entry;
    this.startedAt = this.activeSource.kukichaStartTime;
    this.startOffsetSeconds = 0;
    this.positionMs = 0;
    this.paused = false;
    handleEnginePlaybackStarted(entry.track, entry.position);
    this.prefetchNextTrack();
    this.scheduleNextTrack();
    this.startProgressLoop();
  }

  createSource(entry, startTime, offsetSeconds) {
    const source = this.context.createBufferSource();
    source.buffer = entry.buffer;
    source.kukichaEntry = entry;
    source.kukichaStartTime = startTime;
    source.kukichaStopped = false;
    source.kukichaHandled = false;
    source.connect(this.gainNode);
    source.onended = () => this.handleSourceEnded(source);
    source.start(
      startTime,
      this.bufferOffsetForEntry(entry, offsetSeconds),
      Math.max(
        webAudioMinimumSourceDurationSeconds,
        this.playableDuration(entry) - offsetSeconds
      )
    );
    return source;
  }

  switchHtmlToWebAudio(entry) {
    if (!this.htmlAudioActive || !entry || !entry.buffer || this.currentEntry !== entry) {
      return;
    }
    this.startEntry(entry, this.currentTimeSeconds(), {notifyStart: false});
  }

  handleHtmlAudioEnded() {
    if (!this.htmlAudioActive || !this.currentEntry) {
      return false;
    }
    const finishedEntry = this.currentEntry;
    this.htmlAudioActive = false;
    this.positionMs = this.playableDuration(finishedEntry) * 1000;
    handleEngineTrackFinished(finishedEntry.track);
    const nextEntry = this.nextEntryAfter(finishedEntry.position);
    if (nextEntry && nextEntry.buffer) {
      this.startEntry(nextEntry, 0);
      return true;
    }
    if (nextEntry) {
      this.waitingForNext = true;
      this.stopProgressLoop();
      this.startEntryWhenReady(nextEntry, this.loadToken, finishedEntry.position);
      return true;
    }
    this.paused = true;
    this.stopProgressLoop();
    handleEngineFinishedAll();
    return true;
  }

  handleSourceEnded(source) {
    if (
      !source
      || source.kukichaStopped
      || source.kukichaHandled
      || this.suppressCallbacks
      || this.paused
    ) {
      return;
    }
    const entry = source.kukichaEntry;
    if (source === this.activeSource) {
      const nextEntry = this.nextEntryAfter(entry.position);
      this.finishEntryAndAdvance(entry, nextEntry, this.loadToken);
    }
  }

  startProgressLoop() {
    this.stopProgressLoop();
    const tick = () => {
      if (this.paused) {
        this.progressFrame = 0;
        return;
      }
      this.positionMs = this.currentTimeSeconds() * 1000;
      updatePlaybackProgress();
      this.progressFrame = window.requestAnimationFrame(tick);
    };
    this.progressFrame = window.requestAnimationFrame(tick);
  }

  stopProgressLoop() {
    if (!this.progressFrame) {
      return;
    }
    window.cancelAnimationFrame(this.progressFrame);
    this.progressFrame = 0;
  }

  stopScheduledPlayback(options = {}) {
    this.clearTransitionTimer();
    this.stopSource(this.nextSource);
    this.nextSource = null;
    this.stopSource(this.activeSource);
    this.activeSource = null;
    this.stopHtmlAudio(options);
    this.stopProgressLoop();
  }

  stopHtmlAudio(options = {}) {
    if (!this.htmlAudioActive && !options.clearHtmlSource) {
      return;
    }
    this.htmlAudioActive = false;
    try {
      audio.pause();
      if (options.clearHtmlSource) {
        audio.removeAttribute("src");
        audio.load();
      }
    } catch {
      return;
    }
  }

  stopSource(source) {
    if (!source || source.kukichaStopped) {
      return;
    }
    source.kukichaStopped = true;
    try {
      source.stop(0);
    } catch {
      // AudioBufferSourceNode.stop() throws if the source has not been started.
    }
    try {
      source.disconnect();
    } catch {
      // Some test doubles and older browsers do not expose disconnect().
    }
  }

  markSourceHandled(source) {
    if (source) {
      source.kukichaHandled = true;
    }
  }

  clearTransitionTimer() {
    if (!this.transitionTimer) {
      return;
    }
    window.clearTimeout(this.transitionTimer);
    this.transitionTimer = 0;
  }

  clampEntryOffset(entry, seconds) {
    const duration = this.playableDuration(entry);
    const maximum = duration === null
      ? Number.MAX_SAFE_INTEGER
      : Math.max(0, duration - webAudioMinimumSourceDurationSeconds);
    return clampNumber(Number(seconds), 0, maximum);
  }

  playableDuration(entry) {
    if (entry && entry.buffer) {
      const trim = entry.trim || {start: 0, end: 0};
      return Math.max(
        webAudioMinimumSourceDurationSeconds,
        Number(entry.buffer.duration) - trim.start - trim.end
      );
    }
    return trackDurationSeconds(entry && entry.track);
  }

  bufferOffsetForEntry(entry, offsetSeconds) {
    const trim = entry.trim || {start: 0, end: 0};
    const latestOffset = Math.max(
      trim.start,
      Number(entry.buffer.duration) - trim.end - webAudioMinimumSourceDurationSeconds
    );
    return clampNumber(trim.start + Number(offsetSeconds), trim.start, latestOffset);
  }

  nextEntryAfter(position) {
    const index = this.segment.findIndex((entry) => entry.position === position);
    return index === -1 ? null : this.segment[index + 1] || null;
  }

  entryForPosition(position) {
    return this.segment.find((entry) => entry.position === position) || null;
  }
}

const nativeAudioEngine = new NativeAudioEngine(audio);
const webAudioPlaybackEngine = new WebAudioPlaybackEngine();
activePlaybackEngine = nativeAudioEngine;

function readToastDelayMs(datasetKey, fallback) {
  if (!(toast instanceof HTMLElement)) {
    return fallback;
  }
  const value = Number(toast.dataset[datasetKey]);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

initializeHistoryState();
syncFilterSummaries();
syncAlbumMusicBrainzFormValues();
syncAlbumEditAlbumLevelFields();
syncAlbumArtistMappingForms();
localizeBrowserTimes();
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
    return emptyQueueState();
  }
  try {
    return normalizeQueueState(JSON.parse(source.textContent));
  } catch {
    return emptyQueueState();
  }
}

function normalizeQueueState(state) {
  const trackIds = state && Array.isArray(state.track_ids)
    ? state.track_ids.map(Number).filter(Number.isFinite)
    : [];
  if (!trackIds.length) {
    return emptyQueueState();
  }
  hydrateQueueStateSnapshots(state, trackIds);
  const validTrackIds = new Set(trackIds);
  const erroredTrackIds = state && Array.isArray(state.errored_track_ids)
    ? Array.from(new Set(
        state.errored_track_ids
          .map(Number)
          .filter((trackId) => Number.isFinite(trackId) && validTrackIds.has(trackId))
      ))
    : [];
  const unavailableTrackIds = state && Array.isArray(state.unavailable_track_ids)
    ? Array.from(new Set(
        state.unavailable_track_ids
          .map(Number)
          .filter((trackId) => Number.isFinite(trackId) && validTrackIds.has(trackId))
      ))
    : [];
  const unavailableTrackIdSet = new Set(unavailableTrackIds);
  const loadedTrackIdValue = state && state.loaded_track_id === null
    ? null
    : Number(state && state.loaded_track_id);
  let position = Number(state && state.position);
  position = Number.isFinite(position) ? Math.trunc(position) : 0;
  position = Math.max(0, Math.min(position, trackIds.length));
  let loadedTrackId = Number.isFinite(loadedTrackIdValue) ? loadedTrackIdValue : null;
  if (
    loadedTrackId !== null
    && (!trackIds.includes(loadedTrackId) || unavailableTrackIdSet.has(loadedTrackId))
  ) {
    loadedTrackId = null;
  }
  if (loadedTrackId === null) {
    for (let index = position; index < trackIds.length; index += 1) {
      if (!unavailableTrackIdSet.has(trackIds[index])) {
        loadedTrackId = trackIds[index];
        position = index;
        break;
      }
    }
  }
  return {
    track_ids: trackIds,
    position,
    loaded_track_id: loadedTrackId,
    paused: loadedTrackId === null ? true : !state || state.paused !== false,
    errored_track_ids: erroredTrackIds,
    unavailable_track_ids: unavailableTrackIds
  };
}

function hydrateQueueStateSnapshots(state, trackIds) {
  const snapshots = state && Array.isArray(state.track_snapshots)
    ? state.track_snapshots
    : [];
  if (!snapshots.length) {
    return;
  }
  const validTrackIds = new Set(trackIds);
  cacheTracks(
    snapshots
      .map(normalizeTrackPayload)
      .filter((track) => track && validTrackIds.has(track.trackId))
  );
}

function emptyQueueState() {
  return {
    track_ids: [],
    position: 0,
    loaded_track_id: null,
    paused: true,
    errored_track_ids: [],
    unavailable_track_ids: []
  };
}

function syncAlbumMusicBrainzFormValues() {
  view.querySelectorAll(
    "[data-musicbrainz-url-input], [data-musicbrainz-release-mbid-input], [data-musicbrainz-release-group-mbid-input]"
  ).forEach((input) => {
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    const serverValue = input.getAttribute("data-server-value");
    input.value = serverValue === null ? input.defaultValue : serverValue;
  });
}

function syncAlbumEditAlbumLevelFields(scope = view) {
  if (!(scope instanceof Element)) {
    return;
  }
  const forms = scope instanceof HTMLFormElement && scope.hasAttribute("data-album-edit-form")
    ? [scope]
    : Array.from(scope.querySelectorAll("form[data-album-edit-form]"));
  forms.forEach((form) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const groupCount = form.querySelectorAll("[data-musicbrainz-group]").length;
    if (groupCount > 1) {
      return;
    }
    const musicBrainzUrlInput = form.querySelector("[data-musicbrainz-url-input]");
    const hasMusicBrainzUrl = (
      musicBrainzUrlInput instanceof HTMLInputElement
      && musicBrainzUrlInput.value.trim() !== ""
    );
    [
      form.querySelector("[data-album-input]"),
      form.querySelector("[data-album-artist-input]"),
      form.querySelector("[data-album-genre-input]")
    ].forEach((input) => {
      if (input instanceof HTMLInputElement) {
        input.disabled = hasMusicBrainzUrl;
      }
    });
    const note = form.querySelector("[data-album-level-musicbrainz-note]");
    if (note instanceof HTMLElement) {
      note.hidden = !hasMusicBrainzUrl;
    }
  });
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
  const html = await response.text();
  if (!response.ok && response.status !== 404) {
    throw new Error(`fragment request failed: ${response.status}`);
  }
  return html;
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
  syncAlbumMusicBrainzFormValues();
  syncAlbumEditAlbumLevelFields();
  syncAlbumArtistMappingForms();
  localizeBrowserTimes();
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
  syncTopLevelHiddenInputs(currentForm, nextForm);
  syncReadonlyArtistFilter(currentForm, nextForm);
  syncFormControls(currentForm, nextForm);
}

function syncReadonlyArtistFilter(currentForm, nextForm) {
  const currentFilter = currentForm.querySelector("[data-readonly-artist-filter]");
  const nextFilter = nextForm.querySelector("[data-readonly-artist-filter]");
  if (currentFilter instanceof HTMLElement && nextFilter instanceof HTMLElement) {
    currentFilter.parentNode?.insertBefore(nextFilter, currentFilter);
    currentFilter.remove();
    return;
  }
  if (currentFilter instanceof HTMLElement) {
    currentFilter.remove();
    return;
  }
  if (!(nextFilter instanceof HTMLElement)) {
    return;
  }
  const controls = currentForm.querySelector(".search-controls");
  if (!(controls instanceof HTMLElement)) {
    return;
  }
  controls.insertBefore(nextFilter, controls.children[0] || null);
}

function syncTopLevelHiddenInputs(currentForm, nextForm) {
  const currentInputs = topLevelHiddenInputs(currentForm);
  const nextInputs = topLevelHiddenInputs(nextForm);
  for (const input of currentInputs) {
    input.remove();
  }
  const replacements = nextInputs.map((input) => {
    const replacement = document.createElement("input");
    replacement.type = "hidden";
    replacement.name = input.name;
    replacement.value = input.value;
    return replacement;
  });
  if (replacements.length) {
    currentForm.prepend(...replacements);
  }
}

function topLevelHiddenInputs(form) {
  return Array.from(form.children).filter((child) => (
    child instanceof HTMLInputElement
    && child.type === "hidden"
    && Boolean(child.name)
  ));
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
    if (shouldSyncFormControl(control)) {
      nextControls.set(formControlKey(control), control);
    }
  }
  for (const control of currentForm.elements) {
    if (!shouldSyncFormControl(control)) {
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

function shouldSyncFormControl(control) {
  return isSyncableFormControl(control)
    && (Boolean(control.name) || isGenreParentControl(control));
}

function isGenreParentControl(control) {
  return control instanceof HTMLInputElement
    && control.matches("[data-genre-parent-control]");
}

function formControlKey(control) {
  const name = control.name || (isGenreParentControl(control) ? "data-genre-parent-control" : "");
  const value = control instanceof HTMLInputElement
    && (control.type === "checkbox" || control.type === "radio")
    ? control.value
    : "";
  return `${control.tagName}:${name}:${value}`;
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
  updateSearchSummary(form);
  updateSortSummary(form);
  updateFilterSummary(form, "genres", selectedGenreFilterCount(form));
}

function updateSearchSummary(form) {
  const summary = form.querySelector('[data-filter-summary="search"]');
  if (!(summary instanceof HTMLElement)) {
    return;
  }
  const label = summary.dataset.summaryLabel || "Search";
  const input = form.querySelector('input[name="search"]');
  const value = input instanceof HTMLInputElement ? input.value.trim() : "";
  summary.replaceChildren();
  const labelElement = document.createElement("span");
  labelElement.className = "search-menu-label";
  labelElement.textContent = value ? `${label}:` : label;
  summary.append(labelElement);
  if (value) {
    const valueElement = document.createElement("span");
    valueElement.className = "search-menu-value";
    valueElement.textContent = value;
    summary.append(valueElement);
  }
  summary.title = value ? `${label}: ${value}` : label;
}

function updateSortSummary(form) {
  const summary = form.querySelector('[data-filter-summary="sort"]');
  if (!(summary instanceof HTMLElement)) {
    return;
  }
  const label = summary.dataset.summaryLabel || "Sort";
  const input = form.querySelector('input[name="sort"]:checked');
  const optionLabel = sortOptionLabel(input);
  summary.replaceChildren();
  const labelElement = document.createElement("span");
  labelElement.className = "sort-menu-label";
  labelElement.textContent = `${label}:`;
  summary.append(labelElement);
  if (optionLabel) {
    const valueElement = document.createElement("span");
    valueElement.className = "sort-menu-value";
    valueElement.textContent = optionLabel;
    summary.append(valueElement);
  }
  summary.title = optionLabel ? `${label}: ${optionLabel}` : label;
}

function sortOptionLabel(input) {
  if (!(input instanceof HTMLInputElement)) {
    return "";
  }
  const option = input.closest(".filter-option");
  const label = option ? option.querySelector("span") : null;
  if (label instanceof HTMLElement) {
    return label.textContent.trim();
  }
  return input.value.trim();
}

function updateFilterSummary(form, key, count) {
  const summary = form.querySelector(`[data-filter-summary="${key}"]`);
  if (!(summary instanceof HTMLElement)) {
    return;
  }
  const label = summary.dataset.summaryLabel || "";
  summary.textContent = count ? `${label}: ${compactCount(count)}` : label;
}

function compactCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count)) {
    return String(value);
  }
  const wholeCount = Math.trunc(count);
  if (wholeCount < 0) {
    return `-${compactCount(Math.abs(wholeCount))}`;
  }
  if (wholeCount < 1000) {
    return String(wholeCount);
  }
  if (wholeCount > 999000000000000) {
    return "infinity";
  }
  const units = [
    [1000, "k"],
    [1000000, "M"],
    [1000000000, "B"],
    [1000000000000, "T"]
  ];
  let unitIndex = 0;
  for (let index = 0; index < units.length; index += 1) {
    if (wholeCount >= units[index][0]) {
      unitIndex = index;
    }
  }
  while (unitIndex < units.length) {
    const [unit, suffix] = units[unitIndex];
    const rendered = compactCountForUnit(wholeCount, unit);
    if (rendered.value < 1000) {
      return `${trimCompactCount(rendered.value, rendered.precision)}${suffix}`;
    }
    unitIndex += 1;
  }
  return "infinity";
}

function compactCountForUnit(count, unit) {
  const scaled = count / unit;
  const precision = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
  const factor = 10 ** precision;
  return {
    value: Math.round((scaled + Number.EPSILON) * factor) / factor,
    precision
  };
}

function trimCompactCount(value, precision) {
  return value.toFixed(precision).replace(/\.?0+$/, "");
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
  const confirmationCancelButton = event.target.closest("[data-confirmation-cancel]");
  if (confirmationCancelButton) {
    event.preventDefault();
    closeConfirmationDialog(false);
    return;
  }
  const confirmationConfirmButton = event.target.closest("[data-confirmation-confirm]");
  if (confirmationConfirmButton) {
    event.preventDefault();
    closeConfirmationDialog(true);
    return;
  }
  if (event.target.matches("[data-confirmation-dialog]")) {
    event.preventDefault();
    closeConfirmationDialog(false);
    return;
  }
  const closeKeyboardShortcutsButton = event.target.closest("[data-close-keyboard-shortcuts]");
  if (closeKeyboardShortcutsButton) {
    event.preventDefault();
    closeKeyboardShortcutsDialog();
    return;
  }
  if (event.target.matches("[data-keyboard-shortcuts-dialog]")) {
    event.preventDefault();
    closeKeyboardShortcutsDialog();
    return;
  }
  const openKeyboardShortcutsButton = event.target.closest("[data-open-keyboard-shortcuts]");
  if (openKeyboardShortcutsButton) {
    event.preventDefault();
    const menu = openKeyboardShortcutsButton.closest("details");
    const returnFocus = menu instanceof HTMLDetailsElement
      ? menu.querySelector("summary")
      : null;
    closeOpenDropdownMenus();
    showKeyboardShortcutsDialog(returnFocus);
    return;
  }
  const continuePlayToggle = event.target.closest("[data-continue-play-toggle]");
  if (continuePlayToggle) {
    event.preventDefault();
    togglePlayback();
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
    void rescanLibrary(rescanLibraryButton);
    return;
  }
  const clearCacheButton = event.target.closest("[data-clear-cache]");
  if (clearCacheButton) {
    event.preventDefault();
    void clearCache(clearCacheButton);
    return;
  }
  const resetListeningDataButton = event.target.closest("[data-reset-listening-data]");
  if (resetListeningDataButton) {
    event.preventDefault();
    void resetListeningData(resetListeningDataButton);
    return;
  }
  const deleteMusicBrainzOverrideButton = event.target.closest("[data-delete-musicbrainz-override]");
  if (deleteMusicBrainzOverrideButton) {
    event.preventDefault();
    void deleteMusicBrainzOverride(deleteMusicBrainzOverrideButton);
    return;
  }
  const deleteAlbumButton = event.target.closest("[data-delete-album]");
  if (deleteAlbumButton) {
    event.preventDefault();
    void deleteAlbum(deleteAlbumButton);
    return;
  }
  const deletePlaylistButton = event.target.closest("[data-delete-playlist]");
  if (deletePlaylistButton) {
    event.preventDefault();
    void deletePlaylist(deletePlaylistButton);
    return;
  }
  const uploadAlbumCoverButton = event.target.closest("[data-upload-album-cover]");
  if (uploadAlbumCoverButton) {
    event.preventDefault();
    void uploadAlbumCover(uploadAlbumCoverButton);
    return;
  }
  const uploadPlaylistCoverButton = event.target.closest("[data-upload-playlist-cover]");
  if (uploadPlaylistCoverButton) {
    event.preventDefault();
    void uploadPlaylistCover(uploadPlaylistCoverButton);
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

  const albumStarToggle = event.target.closest("[data-album-star-toggle]");
  if (albumStarToggle) {
    event.preventDefault();
    void toggleAlbumStar(albumStarToggle);
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

document.addEventListener("keydown", (event) => {
  handleKeyboardShortcut(event);
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
  const trackPlaylistCreateForm = event.target.closest("form[data-playlist-create-for-track]");
  if (trackPlaylistCreateForm) {
    event.preventDefault();
    void submitTrackPlaylistCreateForm(trackPlaylistCreateForm);
    return;
  }
  const playlistCreateForm = event.target.closest("form[data-playlist-create-form]");
  if (playlistCreateForm) {
    event.preventDefault();
    void submitPlaylistCreateForm(playlistCreateForm);
    return;
  }
  const playlistImportForm = event.target.closest("form[data-playlist-import-form]");
  if (playlistImportForm) {
    event.preventDefault();
    void submitPlaylistImportForm(playlistImportForm);
    return;
  }
  const albumEditForm = event.target.closest("form[data-album-edit-form]");
  if (albumEditForm) {
    event.preventDefault();
    submitAlbumEditForm(albumEditForm);
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
  closeSearchMenu(form);
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
  if (
    event.target instanceof HTMLInputElement
    && event.target.hasAttribute("data-musicbrainz-url-input")
  ) {
    const albumEditForm = event.target.closest("form[data-album-edit-form]");
    if (albumEditForm) {
      syncAlbumEditAlbumLevelFields(albumEditForm);
    }
  }
  const albumArtistMappingForm = event.target.closest("form[data-album-artist-mapping-form]");
  if (albumArtistMappingForm) {
    syncAlbumArtistMappingFormState(albumArtistMappingForm);
    return;
  }
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
  if (event.target.matches("[data-search-menu]")) {
    focusSearchMenuInput(event.target);
  }
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
  releaseAudioNetworkResources();
  saveCurrentScrollState();
  closeJobsStream();
});

window.addEventListener("pageshow", (event) => {
  pageIsUnloading = false;
  if (!event.persisted) {
    syncAlbumMusicBrainzFormValues();
    syncAlbumEditAlbumLevelFields();
  }
  syncJobsStream();
  updatePlaybackUi();
});

window.addEventListener("beforeunload", () => {
  releaseAudioNetworkResources();
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
    activePlaybackEngine.pause();
    return;
  }
  if (queueIsExhausted()) {
    const firstPlayablePosition = nextPlayableQueuePosition(-1);
    if (firstPlayablePosition !== -1) {
      playQueuePosition(firstPlayablePosition);
    }
    return;
  }
  const controlsPosition = queuePositionForControls();
  const queuedTrackId = queueState.track_ids[controlsPosition] ?? null;
  const firstPlayablePosition = nextPlayableQueuePosition(-1);
  const trackId = loadedId !== null && trackIsPlayable(loadedId)
    ? loadedId
    : queuedTrackId !== null && trackIsPlayable(queuedTrackId)
      ? queuedTrackId
      : firstPlayablePosition === -1
        ? null
        : queueState.track_ids[firstPlayablePosition];
  if (trackId === null) {
    return;
  }
  playTrack(trackById(trackId), {restart: false});
}

function queueIsExhausted() {
  return queueState.track_ids.length > 0 && queueState.position >= queueState.track_ids.length;
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
    if (!activePlaybackEngine.isPaused()) {
      return;
    }
    if (suppressPauseStateUntilPlay || activePlaybackEngine.isSeeking()) {
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
    activePlaybackEngine.isPaused()
    && !suppressPauseStateUntilPlay
    && !pendingPauseCommitTimeout
  );
}

function playbackIsActive() {
  const loadedId = loadedTrackId();
  return loadedId !== null && trackIsPlayable(loadedId) && !playbackPausedForUi();
}

function clampNumber(value, min, max) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, value));
}

function finiteAudioDuration() {
  return activePlaybackEngine.durationSeconds();
}

function trackDurationSeconds(track) {
  if (trackDurationIsIndeterminate(track)) {
    return null;
  }
  const duration = Number(track && track.durationSeconds);
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

function trackDurationIsIndeterminate(track) {
  return Boolean(track && track.durationIsIndeterminate);
}

function currentQueueTrackForProgress() {
  const loadedId = loadedTrackId();
  if (loadedId !== null) {
    return trackById(loadedId);
  }
  if (!queueState.track_ids.length) {
    return null;
  }
  const trackId = queueState.track_ids[queuePositionForControls()];
  return Number.isFinite(Number(trackId)) ? trackById(trackId) : null;
}

function playbackDurationForProgress(track) {
  if (trackDurationIsIndeterminate(track)) {
    return null;
  }
  return finiteAudioDuration() || trackDurationSeconds(track);
}

function finiteAudioCurrentTime() {
  return activePlaybackEngine.currentTimeSeconds();
}

function formatMediaTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "--:--";
  }
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainingSeconds = wholeSeconds % 60;
  const paddedSeconds = String(remainingSeconds).padStart(2, "0");
  if (!hours) {
    return `${minutes}:${paddedSeconds}`;
  }
  return `${hours}:${String(minutes).padStart(2, "0")}:${paddedSeconds}`;
}

function updateRangeFill(input, fraction) {
  if (!(input instanceof HTMLInputElement)) {
    return;
  }
  const percent = clampNumber(fraction, 0, 1) * 100;
  input.style.setProperty("--range-fill", `${percent}%`);
}

function createDurationInfinityIcon() {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("class", "duration-infinity-icon");
  icon.setAttribute("width", "16");
  icon.setAttribute("height", "16");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("d", durationInfinityIconPathData);
  icon.append(path);
  return icon;
}

function updateDurationTimeLabel(duration, isIndeterminate) {
  if (!(durationTime instanceof HTMLElement)) {
    return;
  }
  if (isIndeterminate) {
    if (durationTime.dataset.durationIsIndeterminate !== "1") {
      durationTime.replaceChildren(createDurationInfinityIcon());
      durationTime.dataset.durationIsIndeterminate = "1";
      durationTime.setAttribute("aria-label", "Indeterminate duration");
      durationTime.setAttribute("title", "Indeterminate duration");
    }
    return;
  }
  delete durationTime.dataset.durationIsIndeterminate;
  durationTime.removeAttribute("aria-label");
  durationTime.removeAttribute("title");
  durationTime.textContent = duration === null ? "--:--" : formatMediaTime(duration);
}

function updatePlaybackProgress() {
  const track = currentQueueTrackForProgress();
  const isIndeterminate = trackDurationIsIndeterminate(track);
  const duration = playbackDurationForProgress(track);
  const currentTime = duration === null
    ? finiteAudioCurrentTime()
    : clampNumber(activePlaybackEngine.currentTimeSeconds(), 0, duration);
  if (elapsedTime instanceof HTMLElement) {
    elapsedTime.textContent = formatMediaTime(currentTime);
  }
  updateDurationTimeLabel(duration, isIndeterminate);
  if (!(progressInput instanceof HTMLInputElement)) {
    return;
  }
  const max = Number(progressInput.max) || 1000;
  const canSeek = duration !== null;
  progressInput.disabled = !canSeek;
  if (!canSeek) {
    progressInput.value = "0";
    progressInput.setAttribute(
      "aria-valuetext",
      isIndeterminate ? "Indeterminate duration" : "No seekable duration"
    );
    updateRangeFill(progressInput, 0);
    return;
  }
  const fraction = clampNumber(currentTime / duration, 0, 1);
  progressInput.value = String(Math.round(fraction * max));
  progressInput.setAttribute(
    "aria-valuetext",
    `${formatMediaTime(currentTime)} of ${formatMediaTime(duration)}`
  );
  updateRangeFill(progressInput, fraction);
}

function seekFromProgressInput() {
  if (!(progressInput instanceof HTMLInputElement)) {
    return;
  }
  const track = currentQueueTrackForProgress();
  const duration = playbackDurationForProgress(track);
  if (duration === null) {
    updatePlaybackProgress();
    return;
  }
  const max = Number(progressInput.max) || 1000;
  const fraction = clampNumber(Number(progressInput.value) / max, 0, 1);
  try {
    activePlaybackEngine.seek(duration * fraction);
  } catch {
    updatePlaybackProgress();
    return;
  }
  updatePlaybackProgress();
}

function effectivePlaybackVolume() {
  const volume = clampNumber(Number(audio.volume), 0, 1);
  return audio.muted ? 0 : volume;
}

function applyActiveEngineVolume() {
  if (activePlaybackEngine) {
    activePlaybackEngine.setVolume(effectivePlaybackVolume());
  }
}

function updateVolumeControl() {
  if (!(volumeInput instanceof HTMLInputElement)) {
    return;
  }
  const volume = clampNumber(Number(audio.volume), 0, 1);
  volumeInput.value = String(volume);
  updateRangeFill(volumeInput, volume);
  updateVolumeIcon(volume);
}

function setVolumeFromInput() {
  if (!(volumeInput instanceof HTMLInputElement)) {
    return;
  }
  const volume = clampNumber(Number(volumeInput.value), 0, 1);
  audio.volume = volume;
  applyActiveEngineVolume();
  updateRangeFill(volumeInput, volume);
  updateVolumeIcon(volume);
}

function toggleMuted() {
  audio.muted = !audio.muted;
  applyActiveEngineVolume();
  updateVolumeIcon(audio.volume);
}

function updateVolumeIcon(volume) {
  if (!(volumeIcon instanceof SVGSVGElement)) {
    return;
  }
  const mutedIcon = audio.muted || volume <= 0;
  const pathData = mutedIcon ? volumeMutedIconPathData : volumeIconPathData;
  volumeIcon.dataset.state = mutedIcon ? "muted" : "volume";
  volumeIcon.replaceChildren(...pathData.map(svgPath));
  if (volumeToggle instanceof HTMLButtonElement) {
    const label = audio.muted ? "Unmute volume" : "Mute volume";
    volumeToggle.setAttribute("aria-label", label);
    volumeToggle.setAttribute("aria-pressed", String(audio.muted));
    volumeToggle.title = label;
  }
}

function svgPath(pathData) {
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  return path;
}

function updatePlayToggleButton(button, playing, ariaSuffix = "") {
  if (!(button instanceof HTMLElement)) {
    return;
  }
  const label = playing ? "Pause" : "Play";
  button.setAttribute("aria-label", ariaSuffix ? `${label} ${ariaSuffix}` : label);
  button.setAttribute("aria-pressed", String(playing));
  button.title = label;
}

function setPlayToggleIcons(selector, hidden) {
  for (const icon of document.querySelectorAll(selector)) {
    icon.hidden = hidden;
  }
}

function updatePlayButton(playing) {
  updatePlayToggleButton(playButton, playing);
  for (const button of document.querySelectorAll("[data-continue-play-toggle]")) {
    updatePlayToggleButton(button, playing, "current queue");
  }
  setPlayToggleIcons("[data-play-icon]", playing);
  setPlayToggleIcons("[data-pause-icon]", !playing);
}

previousButton.addEventListener("click", () => {
  moveQueue(-1);
});

nextButton.addEventListener("click", () => {
  moveQueue(1);
});

if (progressInput instanceof HTMLInputElement) {
  progressInput.addEventListener("input", seekFromProgressInput);
  progressInput.addEventListener("change", seekFromProgressInput);
}

if (volumeInput instanceof HTMLInputElement) {
  volumeInput.addEventListener("input", setVolumeFromInput);
  volumeInput.addEventListener("change", setVolumeFromInput);
}

if (volumeToggle instanceof HTMLButtonElement) {
  volumeToggle.addEventListener("click", toggleMuted);
}

function updateNativePlaybackProgress() {
  if (activePlaybackEngine === nativeAudioEngine) {
    updatePlaybackProgress();
  }
}

function handleEnginePaused() {
  if (pageIsUnloading) {
    return;
  }
  if (manualPauseRequested) {
    manualPauseRequested = false;
    clearPauseStateSuppression();
    commitPausedState();
    return;
  }
  schedulePauseCommit();
  updatePlaybackUi();
}

function handleEnginePlaybackStarted(track, position) {
  if (pageIsUnloading || !track) {
    return;
  }
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  queueState.position = position;
  queueState.loaded_track_id = track.trackId;
  queueState.paused = false;
  updateNowPlaying(track);
  const startKey = playbackStartKey(position, track.trackId);
  if (pendingEngineStartKey === startKey) {
    pendingEngineStartKey = "";
    updatePlaybackUi();
    return;
  }
  void recordPlaybackStart(track);
  updatePlaybackUi();
}

function handleEngineTrackFinished(track) {
  if (!track) {
    return;
  }
  if (trackDurationIsIndeterminate(track)) {
    submitIndeterminatePlayedScrobble(track);
  } else {
    postScrobble(track.trackId, true);
  }
}

function handleEngineFinishedAll() {
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
}

function handleBufferedPlaybackError(entry, error) {
  if (!entry || !entry.track) {
    return;
  }
  const loadedPosition = queueLoadedPosition();
  if (entry.position !== loadedPosition) {
    const wasAlreadyErrored = trackHasPlaybackError(entry.track.trackId);
    addPlaybackError(entry.track.trackId);
    if (!wasAlreadyErrored) {
      showToast(
        playbackFailureToastMessage(entry.track, playbackErrorMessage(entry.track, error)),
        {error: true, persistent: true}
      );
    }
    postPlayback({
      loaded_track_id: loadedTrackId(),
      paused: queueState.paused,
      errored_track_ids: queueState.errored_track_ids
    });
    updatePlaybackUi();
    return;
  }
  handlePlaybackFailure(entry.track, playbackErrorMessage(entry.track, error));
}

audio.addEventListener("timeupdate", updateNativePlaybackProgress);
audio.addEventListener("loadedmetadata", updateNativePlaybackProgress);
audio.addEventListener("durationchange", updateNativePlaybackProgress);
audio.addEventListener("emptied", updateNativePlaybackProgress);
audio.addEventListener("volumechange", updateVolumeControl);

audio.addEventListener("seeking", () => {
  if (activePlaybackEngine !== nativeAudioEngine) {
    return;
  }
  if (loadedTrackId() === null || queueState.paused) {
    return;
  }
  suppressPauseStateUntilPlay = true;
  clearPendingPauseCommit();
  updatePlaybackUi();
});

audio.addEventListener("seeked", () => {
  if (activePlaybackEngine !== nativeAudioEngine) {
    return;
  }
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
  if (activePlaybackEngine !== nativeAudioEngine) {
    return;
  }
  if (pageIsUnloading) {
    return;
  }
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  queueState.paused = false;
  postPlayback({paused: false, loaded_track_id: loadedTrackId()});
  updatePlaybackUi();
});

audio.addEventListener("pause", () => {
  if (activePlaybackEngine === nativeAudioEngine) {
    handleEnginePaused();
  }
});

audio.addEventListener("error", () => {
  if (activePlaybackEngine !== nativeAudioEngine) {
    return;
  }
  if (pageIsUnloading) {
    return;
  }
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  const track = trackById(loadedTrackId());
  handlePlaybackFailure(track, playbackErrorMessage(track));
});

audio.addEventListener("ended", () => {
  if (activePlaybackEngine === webAudioPlaybackEngine) {
    webAudioPlaybackEngine.handleHtmlAudioEnded();
    return;
  }
  if (activePlaybackEngine !== nativeAudioEngine) {
    return;
  }
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  const track = trackById(loadedTrackId());
  handleEngineTrackFinished(track);
  handleEngineFinishedAll();
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

async function submitTrackPlaylistCreateForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const trackId = Number(form.dataset.trackId || "");
  const nameInput = form.querySelector("[data-playlist-create-track-name]");
  const submitButton = form.querySelector("[data-create-track-playlist]");
  if (
    !Number.isInteger(trackId)
    || trackId <= 0
    || !(nameInput instanceof HTMLInputElement)
    || !(submitButton instanceof HTMLButtonElement)
  ) {
    return;
  }
  const name = nameInput.value.trim();
  if (!name) {
    setTrackPlaylistCreateStatus(form, "Playlist name is required.", true);
    return;
  }

  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  nameInput.disabled = true;
  setTrackPlaylistCreateStatus(form, "Creating playlist...");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, track_ids: [trackId]}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = responsePayloadError(payload, "Unable to create playlist.");
      setTrackPlaylistCreateStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const playlistId = Number(payload.playlist_id || "");
    if (!Number.isInteger(playlistId) || playlistId <= 0) {
      throw new Error("Playlist was created without a playlist id.");
    }
    const playlistName = String(payload.name || name).trim() || `Playlist ${playlistId}`;
    addCreatedPlaylistOptionToTrackMenus({
      playlistId,
      playlistName,
      selectedTrackId: trackId,
    });
    nameInput.value = "";
    const message = responsePayloadMessage(payload, "Playlist created.");
    setTrackPlaylistCreateStatus(form, message);
    showToast(message);
  } catch (error) {
    const message = error instanceof Error && error.message
      ? error.message
      : "Unable to create playlist.";
    setTrackPlaylistCreateStatus(form, message, true);
    showToast(message, {error: true});
  } finally {
    if (nameInput.isConnected) {
      nameInput.disabled = false;
    }
    if (submitButton.isConnected) {
      submitButton.disabled = false;
      submitButton.removeAttribute("aria-busy");
    }
  }
}

function addCreatedPlaylistOptionToTrackMenus({playlistId, playlistName, selectedTrackId}) {
  document.querySelectorAll("[data-playlist-options]:not([data-playlist-floating-options])").forEach((options) => {
    if (!(options instanceof HTMLElement)) {
      return;
    }
    const trackId = playlistOptionsTrackId(options);
    if (!trackId) {
      return;
    }
    appendPlaylistOption(options, {
      playlistId,
      playlistName,
      trackId,
      checked: trackId === selectedTrackId,
    });
    updatePlaylistBookmarkForOptions(options);
  });
  if (activePlaylistOptions instanceof HTMLElement) {
    appendPlaylistOption(activePlaylistOptions, {
      playlistId,
      playlistName,
      trackId: selectedTrackId,
      checked: true,
    });
    updatePlaylistBookmarkForOptions(activePlaylistOptions);
    positionActivePlaylistMenu();
  }
}

function playlistOptionsTrackId(options) {
  const createForm = options.querySelector("[data-playlist-create-for-track]");
  if (createForm instanceof HTMLFormElement) {
    const trackId = Number(createForm.dataset.trackId || "");
    return Number.isInteger(trackId) && trackId > 0 ? trackId : 0;
  }
  const menu = options.closest("[data-playlist-menu]");
  if (menu instanceof HTMLElement) {
    const trackId = Number(menu.dataset.libraryTrackId || "");
    return Number.isInteger(trackId) && trackId > 0 ? trackId : 0;
  }
  return 0;
}

function appendPlaylistOption(options, {playlistId, playlistName, trackId, checked}) {
  if (!(options instanceof HTMLElement)) {
    return;
  }
  const existingSelector = `[data-playlist-toggle][data-playlist-id="${cssEscape(playlistId)}"]`;
  if (options.querySelector(existingSelector)) {
    return;
  }
  options.querySelectorAll(".empty-small").forEach((empty) => {
    if (empty.textContent.trim() === "No playlists found.") {
      empty.remove();
    }
  });
  const label = document.createElement("label");
  label.className = "filter-option";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.dataset.playlistToggle = "";
  input.dataset.trackId = String(trackId);
  input.dataset.playlistId = String(playlistId);
  input.checked = checked;
  if (checked) {
    input.setAttribute("checked", "");
  }
  const span = document.createElement("span");
  span.textContent = playlistName || `Playlist ${playlistId}`;
  label.append(input, span);
  const createForm = options.querySelector("[data-playlist-create-for-track]");
  if (createForm) {
    createForm.before(label);
  } else {
    options.append(label);
  }
}

function updatePlaylistBookmarkForOptions(options) {
  if (!(options instanceof HTMLElement)) {
    return;
  }
  const hasPlaylistMembership = Array.from(options.querySelectorAll("[data-playlist-toggle]"))
    .some((element) => element instanceof HTMLInputElement && element.checked);
  const menu = options === activePlaylistOptions
    ? activePlaylistMenu
    : options.closest("[data-playlist-menu]");
  setPlaylistBookmarkState(menu, hasPlaylistMembership);
}

function setTrackPlaylistCreateStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-playlist-create-track-status]", message, isError);
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

function localizeBrowserTimes() {
  localizeDateLabels();
  localizeJobTimes();
}

function localizeDateLabels() {
  view.querySelectorAll("[data-local-date-prefix]").forEach((element) => {
    if (!(element instanceof HTMLElement)) {
      return;
    }
    const timestamp = element.getAttribute("datetime") || "";
    const prefix = element.dataset.localDatePrefix || "";
    const dateText = formatBrowserDate(timestamp, "");
    if (!dateText) {
      return;
    }
    element.textContent = prefix ? `${prefix} ${dateText}` : dateText;
  });
  view.querySelectorAll("[data-local-date]").forEach((element) => {
    if (!(element instanceof HTMLElement)) {
      return;
    }
    const timestamp = element.getAttribute("datetime") || "";
    const dateText = formatBrowserDate(timestamp, "");
    if (dateText) {
      element.textContent = dateText;
    }
  });
  view.querySelectorAll("[data-local-date-long]").forEach((element) => {
    if (!(element instanceof HTMLElement)) {
      return;
    }
    const timestamp = element.getAttribute("datetime") || "";
    element.textContent = formatBrowserDateLong(timestamp, element.textContent || timestamp);
  });
  view.querySelectorAll("[data-local-date-time]").forEach((element) => {
    if (!(element instanceof HTMLElement)) {
      return;
    }
    const timestamp = element.getAttribute("datetime") || "";
    element.textContent = formatBrowserDateTime(timestamp, element.textContent || timestamp);
  });
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

function formatBrowserDate(value, fallback = "") {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return fallback || value;
  }
  const year = String(parsed.getFullYear()).padStart(4, "0");
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatBrowserDateLong(value, fallback = "") {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return fallback || value;
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "full"
    }).format(parsed);
  } catch {
    return formatBrowserDate(value, fallback);
  }
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

function albumEditTagPayload(form) {
  const albumInput = form.querySelector("[data-album-input]");
  const genreInput = form.querySelector("[data-album-genre-input]");
  const albumArtistInput = form.querySelector("[data-album-artist-input]");
  const trackRows = Array.from(form.querySelectorAll("[data-track-tag-row]"));
  if (
    !(albumInput instanceof HTMLInputElement)
    || !(genreInput instanceof HTMLInputElement)
    || !(albumArtistInput instanceof HTMLInputElement)
  ) {
    return {payload: null, error: "Album tag fields are unavailable."};
  }

  const tracks = trackRows.map((row) => {
    const artistInput = row.querySelector("[data-track-artist-input]");
    const trackNumberInput = row.querySelector("[data-track-number-input]");
    const titleInput = row.querySelector("[data-track-title-input]");
    if (
      !(artistInput instanceof HTMLInputElement)
      || !(trackNumberInput instanceof HTMLInputElement)
      || !(titleInput instanceof HTMLInputElement)
    ) {
      return null;
    }
    return {
      track_id: Number(row.dataset.trackId || ""),
      artist: artistInput.value.trim(),
      track_number: trackNumberInput.value.trim(),
      title: titleInput.value.trim()
    };
  }).filter((item) => item && Number.isInteger(item.track_id) && item.track_id > 0);
  if (!tracks.length) {
    return {payload: null, error: "No tracks available to edit."};
  }

  return {
    payload: {
      album: albumInput.value.trim(),
      genre: genreInput.value.trim(),
      album_artist: albumArtistInput.value.trim(),
      tracks
    },
    error: ""
  };
}

function albumEditMusicBrainzPayload(form) {
  const groupElements = Array.from(form.querySelectorAll("[data-musicbrainz-group]"));
  const fieldScopes = groupElements.length ? groupElements : [form];
  const requestGroups = [];
  let hasInvalidGroupTracks = false;

  fieldScopes.forEach((scope) => {
    const musicBrainzUrlInput = scope.querySelector("[data-musicbrainz-url-input]");
    const releaseMbidInput = scope.querySelector("[data-musicbrainz-release-mbid-input]");
    const releaseGroupMbidInput = scope.querySelector("[data-musicbrainz-release-group-mbid-input]");
    if (!(musicBrainzUrlInput instanceof HTMLInputElement)
      && !(releaseMbidInput instanceof HTMLInputElement)
      && !(releaseGroupMbidInput instanceof HTMLInputElement)) {
      return;
    }

    const trackIdInputs = Array.from(scope.querySelectorAll("[data-musicbrainz-track-id]"));
    const trackIds = trackIdInputs.map((input) => (
      input instanceof HTMLInputElement ? Number(input.value || "") : NaN
    )).filter((trackId) => Number.isInteger(trackId) && trackId > 0);
    if (trackIdInputs.length && !trackIds.length) {
      hasInvalidGroupTracks = true;
    }

    if (musicBrainzUrlInput instanceof HTMLInputElement) {
      const musicBrainzUrl = musicBrainzUrlInput.value.trim();
      const serverValue = (musicBrainzUrlInput.getAttribute("data-server-value") || "").trim();
      if (!musicBrainzUrl && !serverValue) {
        return;
      }
      requestGroups.push({
        musicbrainz_url: musicBrainzUrl,
        track_ids: trackIds
      });
      return;
    }

    const releaseMbid = releaseMbidInput instanceof HTMLInputElement
      ? releaseMbidInput.value.trim()
      : "";
    const releaseGroupMbid = releaseGroupMbidInput instanceof HTMLInputElement
      ? releaseGroupMbidInput.value.trim()
      : "";
    if (!releaseMbid && !releaseGroupMbid) {
      return;
    }
    requestGroups.push({
      musicbrainz_release_mbid: releaseMbid,
      musicbrainz_release_group_mbid: releaseGroupMbid,
      track_ids: trackIds
    });
  });

  if (hasInvalidGroupTracks) {
    return {payload: null, error: "No tracks available to edit."};
  }
  if (!requestGroups.length) {
    return {payload: null, error: ""};
  }
  return {
    payload: {groups: requestGroups},
    error: ""
  };
}

async function submitAlbumEditForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const submitButtons = Array.from(form.querySelectorAll("[data-apply-album-edit]"))
    .filter((button) => button instanceof HTMLButtonElement);
  if (!submitButtons.length) {
    return;
  }

  const hasTagFields = Boolean(
    form.querySelector("[data-album-input]")
    || form.querySelector("[data-album-artist-input]")
    || form.querySelector("[data-album-genre-input]")
    || form.querySelector("[data-track-tag-row]")
  );
  const tagRequest = hasTagFields
    ? albumEditTagPayload(form)
    : {payload: null, error: ""};
  if (tagRequest.error) {
    setAlbumEditStatus(form, tagRequest.error, true);
    return;
  }
  const musicBrainzRequest = albumEditMusicBrainzPayload(form);
  if (musicBrainzRequest.error) {
    setAlbumEditStatus(form, musicBrainzRequest.error, true);
    return;
  }

  const requestBody = {};
  if (tagRequest.payload) {
    requestBody.tags = tagRequest.payload;
  }
  if (musicBrainzRequest.payload) {
    requestBody.musicbrainz = musicBrainzRequest.payload;
  }
  if (!requestBody.tags && !requestBody.musicbrainz) {
    setAlbumEditStatus(form, "No album edit fields are available.", true);
    return;
  }

  setAlbumEditStatus(form, "Submitting album edit...");
  const formControls = Array.from(form.querySelectorAll("input, textarea, select, button"));
  formControls.forEach((control) => {
    control.disabled = true;
  });
  submitButtons.forEach((button) => {
    button.setAttribute("aria-busy", "true");
  });
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(requestBody)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to apply edit.";
      setAlbumEditStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Tag edit queued.";
    setAlbumEditStatus(form, message);
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    setAlbumEditStatus(form, "Unable to apply edit.", true);
    showToast("Unable to apply edit.", {error: true});
  } finally {
    submitButtons.forEach((button) => {
      button.removeAttribute("aria-busy");
    });
    formControls.forEach((control) => {
      control.disabled = false;
    });
    syncAlbumEditAlbumLevelFields(form);
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

  const confirmed = await confirmAction({
    title: "Delete MusicBrainz Override",
    message: `Delete MusicBrainz override for ${albumId}?`,
    confirmLabel: "Delete",
    returnFocus: button,
  });
  if (!confirmed) {
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

async function deleteAlbum(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const form = button.closest("form[data-album-delete-form]");
  const deleteUrl = button.dataset.deleteUrl;
  const albumLabel = (button.dataset.albumLabel || "this album").trim() || "this album";
  if (!deleteUrl) {
    return;
  }

  const confirmed = await confirmAction({
    title: "Delete Album",
    message: `Delete ${albumLabel}? This removes the audio files and eligible cover art. Rescan Kukicha afterward to prune the library.`,
    confirmLabel: "Delete",
    returnFocus: button,
  });
  if (!confirmed) {
    return;
  }

  if (form instanceof HTMLFormElement) {
    setAlbumDeleteStatus(form, "Submitting album delete...");
  }
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(deleteUrl, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to delete album.";
      if (form instanceof HTMLFormElement) {
        setAlbumDeleteStatus(form, message, true);
      }
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Album delete queued.";
    if (form instanceof HTMLFormElement) {
      setAlbumDeleteStatus(form, message);
    }
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    if (form instanceof HTMLFormElement) {
      setAlbumDeleteStatus(form, "Unable to delete album.", true);
    }
    showToast("Unable to delete album.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function deletePlaylist(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const form = button.closest("form[data-playlist-delete-form]");
  const deleteUrl = button.dataset.deleteUrl;
  const playlistLabel = (button.dataset.playlistLabel || "this playlist").trim() || "this playlist";
  if (!deleteUrl) {
    return;
  }

  const confirmed = await confirmAction({
    title: "Delete Playlist",
    message: `Delete ${playlistLabel}? This removes the playlist from Kukicha.`,
    confirmLabel: "Delete",
    returnFocus: button,
  });
  if (!confirmed) {
    return;
  }

  if (form instanceof HTMLFormElement) {
    setPlaylistDeleteStatus(form, "Deleting playlist...");
  }
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(deleteUrl, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = responsePayloadError(payload, "Unable to delete playlist.");
      if (form instanceof HTMLFormElement) {
        setPlaylistDeleteStatus(form, message, true);
      }
      showToast(message, {error: true});
      return;
    }
    const message = responsePayloadMessage(payload, "Playlist deleted.");
    if (form instanceof HTMLFormElement) {
      setPlaylistDeleteStatus(form, message);
    }
    showToast(message);
    const redirectUrl = typeof payload.redirect_url === "string" && payload.redirect_url.trim()
      ? payload.redirect_url
      : "/playlists";
    await navigate(redirectUrl, {replace: true});
  } catch {
    if (form instanceof HTMLFormElement) {
      setPlaylistDeleteStatus(form, "Unable to delete playlist.", true);
    }
    showToast("Unable to delete playlist.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

function albumCoverUploadExtension(filename) {
  const name = String(filename || "").trim();
  const slashIndex = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  const basename = name.slice(slashIndex + 1);
  const dotIndex = basename.lastIndexOf(".");
  if (dotIndex <= 0 || dotIndex === basename.length - 1) {
    return "";
  }
  const extension = basename.slice(dotIndex).toLowerCase();
  if (![".gif", ".jpeg", ".jpg", ".png", ".webp"].includes(extension)) {
    return "";
  }
  return extension;
}

async function submitPlaylistCreateForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const submitButton = form.querySelector("[data-create-playlist]");
  const nameInput = form.querySelector("[data-playlist-name-input]");
  if (!(submitButton instanceof HTMLButtonElement) || !(nameInput instanceof HTMLInputElement)) {
    return;
  }
  const name = nameInput.value.trim();
  if (!name) {
    setPlaylistCreateStatus(form, "Playlist name is required.", true);
    return;
  }
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  setPlaylistCreateStatus(form, "Creating playlist...");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = responsePayloadError(payload, "Unable to create playlist.");
      setPlaylistCreateStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const message = responsePayloadMessage(payload, "Playlist created.");
    setPlaylistCreateStatus(form, message);
    showToast(message);
    const playlistId = Number(payload.playlist_id || "");
    if (Number.isInteger(playlistId) && playlistId > 0) {
      navigate(`/playlists/${encodeURIComponent(playlistId)}`);
    }
  } catch {
    setPlaylistCreateStatus(form, "Unable to create playlist.", true);
    showToast("Unable to create playlist.", {error: true});
  } finally {
    if (submitButton.isConnected) {
      submitButton.disabled = false;
      submitButton.removeAttribute("aria-busy");
    }
  }
}

async function submitPlaylistImportForm(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const submitButton = form.querySelector("[data-import-playlist]");
  const fileInput = form.querySelector("[data-playlist-file-input]");
  if (!(submitButton instanceof HTMLButtonElement) || !(fileInput instanceof HTMLInputElement)) {
    return;
  }
  const file = fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
  if (!file) {
    setPlaylistImportStatus(form, "Choose a playlist file first.", true);
    return;
  }
  const body = new FormData(form);
  body.delete("playlist");
  body.append("playlist", file, file.name);
  submitButton.disabled = true;
  submitButton.setAttribute("aria-busy", "true");
  fileInput.disabled = true;
  setPlaylistImportStatus(form, "Importing playlist...");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = responsePayloadError(payload, "Unable to import playlist.");
      setPlaylistImportStatus(form, message, true);
      showToast(message, {error: true});
      return;
    }
    const message = responsePayloadMessage(payload, "Playlist imported.");
    setPlaylistImportStatus(form, message);
    showToast(message);
    const playlistId = Number(payload.playlist_id || "");
    if (Number.isInteger(playlistId) && playlistId > 0) {
      navigate(`/playlists/${encodeURIComponent(playlistId)}`);
    }
  } catch {
    setPlaylistImportStatus(form, "Unable to import playlist.", true);
    showToast("Unable to import playlist.", {error: true});
  } finally {
    if (fileInput.isConnected) {
      fileInput.disabled = false;
    }
    if (submitButton.isConnected) {
      submitButton.disabled = false;
      submitButton.removeAttribute("aria-busy");
    }
  }
}

function responsePayloadError(payload, fallback) {
  return payload && typeof payload.error === "string" && payload.error.trim()
    ? payload.error
    : fallback;
}

function responsePayloadMessage(payload, fallback) {
  return payload && typeof payload.message === "string" && payload.message.trim()
    ? payload.message
    : fallback;
}

async function uploadAlbumCover(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const form = button.closest("form[data-album-cover-form]");
  const uploadUrl = button.dataset.uploadUrl;
  const albumLabel = (button.dataset.albumLabel || "this album").trim() || "this album";
  const input = form instanceof HTMLFormElement
    ? form.querySelector("[data-album-cover-input]")
    : null;
  if (!uploadUrl || !(input instanceof HTMLInputElement)) {
    return;
  }

  const file = input.files && input.files.length ? input.files[0] : null;
  if (!file) {
    if (form instanceof HTMLFormElement) {
      setAlbumCoverStatus(form, "Choose a cover image first.", true);
    }
    return;
  }
  const extension = albumCoverUploadExtension(file.name);
  if (!extension) {
    if (form instanceof HTMLFormElement) {
      setAlbumCoverStatus(form, "Cover must be a GIF, JPEG, PNG, or WebP image.", true);
    }
    return;
  }
  const coverFilename = `cover${extension}`;
  const confirmed = await confirmAction({
    title: "Upload Cover",
    message: `Upload ${file.name} as ${coverFilename} for ${albumLabel}? Existing ${coverFilename} files will be overwritten. Rescan Kukicha afterward to reconcile the new cover art.`,
    confirmLabel: "Upload",
    returnFocus: button,
  });
  if (!confirmed) {
    return;
  }

  if (form instanceof HTMLFormElement) {
    setAlbumCoverStatus(form, "Submitting cover upload...");
  }
  input.disabled = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  const body = new FormData();
  body.append("cover", file, file.name);
  try {
    const response = await fetch(uploadUrl, {
      method: "POST",
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to upload cover.";
      if (form instanceof HTMLFormElement) {
        setAlbumCoverStatus(form, message, true);
      }
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Cover upload queued.";
    if (form instanceof HTMLFormElement) {
      setAlbumCoverStatus(form, message);
    }
    if (payload && payload.job) {
      showJobToast(payload.job);
    } else {
      showToast(message);
    }
  } catch {
    if (form instanceof HTMLFormElement) {
      setAlbumCoverStatus(form, "Unable to upload cover.", true);
    }
    showToast("Unable to upload cover.", {error: true});
  } finally {
    if (input.isConnected) {
      input.disabled = false;
    }
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function uploadPlaylistCover(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const form = button.closest("form[data-playlist-cover-form]");
  const uploadUrl = button.dataset.uploadUrl;
  const playlistLabel = (button.dataset.playlistLabel || "this playlist").trim() || "this playlist";
  const input = form instanceof HTMLFormElement
    ? form.querySelector("[data-playlist-cover-input]")
    : null;
  if (!uploadUrl || !(input instanceof HTMLInputElement)) {
    return;
  }

  const file = input.files && input.files.length ? input.files[0] : null;
  if (!file) {
    if (form instanceof HTMLFormElement) {
      setPlaylistCoverStatus(form, "Choose a cover image first.", true);
    }
    return;
  }
  const extension = albumCoverUploadExtension(file.name);
  if (!extension) {
    if (form instanceof HTMLFormElement) {
      setPlaylistCoverStatus(form, "Cover must be a GIF, JPEG, PNG, or WebP image.", true);
    }
    return;
  }
  const confirmed = await confirmAction({
    title: "Upload Cover",
    message: `Upload ${file.name} as the cover for ${playlistLabel}?`,
    confirmLabel: "Upload",
    returnFocus: button,
  });
  if (!confirmed) {
    return;
  }

  if (form instanceof HTMLFormElement) {
    setPlaylistCoverStatus(form, "Uploading cover...");
  }
  input.disabled = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  const body = new FormData();
  body.append("cover", file, file.name);
  try {
    const response = await fetch(uploadUrl, {
      method: "POST",
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = responsePayloadError(payload, "Unable to upload cover.");
      if (form instanceof HTMLFormElement) {
        setPlaylistCoverStatus(form, message, true);
      }
      showToast(message, {error: true});
      return;
    }
    const message = responsePayloadMessage(payload, "Playlist cover updated.");
    if (form instanceof HTMLFormElement) {
      setPlaylistCoverStatus(form, message);
    }
    showToast(message);
    await navigate(window.location.href, {replace: true, scroll: false});
  } catch {
    if (form instanceof HTMLFormElement) {
      setPlaylistCoverStatus(form, "Unable to upload cover.", true);
    }
    showToast("Unable to upload cover.", {error: true});
  } finally {
    if (input.isConnected) {
      input.disabled = false;
    }
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function clearCache(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const clearUrl = button.dataset.clearUrl;
  const label = button.dataset.cacheLabel || "this";
  if (!clearUrl) {
    return;
  }

  const confirmed = await confirmAction({
    title: "Clear Cache",
    message: `Clear ${label} cache?`,
    confirmLabel: "Clear",
    returnFocus: button,
  });
  if (!confirmed) {
    return;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(clearUrl, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to clear cache.";
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Cache cleared.";
    showToast(message);
    await navigate(window.location.href, {replace: true, scroll: false});
  } catch {
    showToast("Unable to clear cache.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function resetListeningData(button) {
  if (!(button instanceof HTMLButtonElement) || button.disabled) {
    return;
  }
  const resetUrl = button.dataset.resetUrl || "/api/listening-data/reset";

  const confirmed = await confirmAction({
    title: "Reset Listening Data",
    message: "Reset listening data?",
    confirmLabel: "Reset",
    returnFocus: button,
  });
  if (!confirmed) {
    return;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(resetUrl, {method: "POST"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload && typeof payload.error === "string" && payload.error.trim()
        ? payload.error
        : "Unable to reset listening data.";
      showToast(message, {error: true});
      return;
    }
    const message = payload && typeof payload.message === "string" && payload.message.trim()
      ? payload.message
      : "Listening data reset.";
    showToast(message);
    await navigate(window.location.href, {replace: true, scroll: false});
  } catch {
    showToast("Unable to reset listening data.", {error: true});
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

async function rescanLibrary(sourceButton = null) {
  const button = sourceButton instanceof HTMLButtonElement ? sourceButton : null;
  if ((button && button.disabled) || rescanLibraryPending) {
    return;
  }

  rescanLibraryPending = true;
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
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
    rescanLibraryPending = false;
    if (button && button.isConnected) {
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

function setAlbumEditStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-edit-status]", message, isError);
}

function setAlbumDeleteStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-delete-status]", message, isError);
}

function setAlbumCoverStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-cover-status]", message, isError);
}

function setPlaylistDeleteStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-playlist-delete-status]", message, isError);
}

function setPlaylistCoverStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-playlist-cover-status]", message, isError);
}

function setPlaylistCreateStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-playlist-create-status]", message, isError);
}

function setPlaylistImportStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-playlist-import-status]", message, isError);
}

function setAlbumArtistMappingStatus(formOrElement, message, isError = false) {
  setStatusMessage(formOrElement, "[data-album-artist-mapping-status]", message, isError);
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
  }, toastHideDelayMs);
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
  if (!shouldApplyJobUpdate(jobId, job)) {
    return;
  }
  const toastElement = jobToastElement(jobId);
  clearJobToastTimeout(toastElement);
  toastElement.className = `job-toast ${status}`;
  toastElement.dataset.jobToastId = String(jobId);
  toastElement.dataset.jobStatus = status;
  toastElement.replaceChildren(...jobToastChildren(job));
  jobToasts.prepend(toastElement);
  rememberJobUpdate(jobId, job);
  if (isTemporaryBookmarkJobToast(job)) {
    const timeout = window.setTimeout(() => {
      removeJobToast(toastElement);
    }, toastHideDelayMs);
    jobToastTimeouts.set(toastElement, timeout);
  }
  updateVisibleJobCard(job);
}

function shouldApplyJobUpdate(jobId, job) {
  const current = latestJobState(jobId);
  if (!current) {
    return true;
  }
  const nextStatus = typeof job.status === "string" ? job.status : "";
  const nextRank = jobStatusRank(nextStatus);
  if (nextRank < current.rank) {
    return false;
  }
  const nextUpdatedAt = jobUpdatedAt(job);
  return !(current.updatedAt && nextUpdatedAt && nextUpdatedAt < current.updatedAt);
}

function latestJobState(jobId) {
  const remembered = jobLatestStates.get(jobId);
  if (remembered) {
    return remembered;
  }
  const renderedStatus = renderedJobStatus(jobId);
  if (!renderedStatus) {
    return null;
  }
  const state = {
    status: renderedStatus,
    rank: jobStatusRank(renderedStatus),
    updatedAt: ""
  };
  jobLatestStates.set(jobId, state);
  return state;
}

function renderedJobStatus(jobId) {
  const toastElement = jobToasts instanceof HTMLElement
    ? jobToasts.querySelector(`[data-job-toast-id="${jobId}"]`)
    : null;
  if (toastElement instanceof HTMLElement) {
    return toastElement.dataset.jobStatus || jobStatusFromClassName(toastElement.className);
  }
  const card = view.querySelector(`[data-job-id="${jobId}"]`);
  if (!(card instanceof HTMLElement)) {
    return "";
  }
  const statusElement = card.querySelector(".job-status");
  return statusElement instanceof HTMLElement
    ? jobStatusFromClassName(statusElement.className)
    : "";
}

function rememberJobUpdate(jobId, job) {
  const status = typeof job.status === "string" ? job.status : "";
  jobLatestStates.set(jobId, {
    status,
    rank: jobStatusRank(status),
    updatedAt: jobUpdatedAt(job)
  });
}

function jobStatusRank(status) {
  return jobStatusRanks.has(status) ? jobStatusRanks.get(status) : 0;
}

function jobUpdatedAt(job) {
  return typeof job.updated_at === "string" ? job.updated_at : "";
}

function jobStatusFromClassName(className) {
  const names = typeof className === "string" ? className.split(/\s+/) : [];
  return names.find((name) => jobStatusRanks.has(name)) || "";
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
  } else if (status === "succeeded" && !isTemporaryBookmarkJobToast(job)) {
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
    removeJobToast(toastElement);
  }
}

function isTemporaryBookmarkJobToast(job) {
  return false;
}

function removeJobToast(toastElement) {
  clearJobToastTimeout(toastElement);
  toastElement.remove();
}

function clearJobToastTimeout(toastElement) {
  const timeout = jobToastTimeouts.get(toastElement);
  if (timeout) {
    clearTimeout(timeout);
    jobToastTimeouts.delete(toastElement);
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
  return url.toString();
}

function albumStarUrl(albumId) {
  const url = new URL(
    `/api/albums/${encodeURIComponent(albumId).replace(/%3A/gi, ":")}/star`,
    window.location.origin
  );
  return url.toString();
}

async function toggleAlbumStar(button) {
  if (!(button instanceof HTMLElement) || button.getAttribute("aria-busy") === "true") {
    return;
  }
  const albumId = button.dataset.albumId || "";
  if (!albumId) {
    return;
  }
  const starred = button.getAttribute("aria-pressed") !== "true";
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(albumStarUrl(albumId), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({starred})
    });
    if (!response.ok) {
      throw new Error(`star request failed: ${response.status}`);
    }
    const payload = await response.json();
    updateAlbumStarButtons(albumId, Boolean(payload.starred));
  } catch {
    showToast("Could not update album favorite.", {error: true});
  } finally {
    button.removeAttribute("aria-busy");
  }
}

function updateAlbumStarButtons(albumId, starred) {
  const selector = `[data-album-star-toggle][data-album-id="${cssEscape(albumId)}"]`;
  document.querySelectorAll(selector).forEach((button) => {
    if (!(button instanceof HTMLElement)) {
      return;
    }
    button.classList.toggle("starred", starred);
    button.setAttribute("aria-pressed", String(starred));
    const title = button.dataset.albumTitle || "album";
    button.setAttribute("aria-label", `${starred ? "Unstar" : "Star"} ${title}`);
    button.title = starred ? "Unstar album" : "Star album";
    const icon = button.querySelector(".star-icon");
    if (icon instanceof SVGElement) {
      icon.classList.toggle("star-icon-filled", starred);
      const shape = icon.querySelector("path");
      if (shape instanceof SVGPathElement) {
        shape.setAttribute("fill", starred ? "currentColor" : "none");
      }
    }
  });
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
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
  const durationIsIndeterminate = Boolean(payload.durationIsIndeterminate);
  const durationSeconds = Number(payload.durationSeconds);
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
    durationSeconds: durationIsIndeterminate || !Number.isFinite(durationSeconds)
      ? null
      : durationSeconds,
    durationIsIndeterminate,
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

function handleKeyboardShortcut(event) {
  if (event.defaultPrevented || event.isComposing) {
    return;
  }
  if (event.key === "Escape") {
    handleEscapeShortcut(event);
    return;
  }
  if (confirmationDialogIsOpen() || keyboardShortcutsDialogIsOpen()) {
    return;
  }
  if (
    event.repeat
    || event.metaKey
    || event.ctrlKey
    || event.altKey
    || isTextInputTarget(event.target)
  ) {
    return;
  }

  if (event.key === "?") {
    event.preventDefault();
    showKeyboardShortcutsDialog();
    return;
  }
  if (event.shiftKey && event.key.toLowerCase() === "r") {
    event.preventDefault();
    void rescanLibrary();
    return;
  }
  if (event.shiftKey) {
    return;
  }

  switch (event.key.toLowerCase()) {
    case "k":
      event.preventDefault();
      togglePlayback();
      break;
    case "j":
      event.preventDefault();
      moveQueue(-1);
      break;
    case "l":
      event.preventDefault();
      moveQueue(1);
      break;
    case "/":
      event.preventDefault();
      void focusSearchShortcut();
      break;
    case "1":
      event.preventDefault();
      navigateToShortcutPage("/");
      break;
    case "2":
      event.preventDefault();
      navigateToShortcutPage("/albums");
      break;
    case "3":
      event.preventDefault();
      navigateToShortcutPage("/artists");
      break;
    case "4":
      event.preventDefault();
      navigateToShortcutPage("/playlists");
      break;
    case "5":
      event.preventDefault();
      navigateToShortcutPage("/queue");
      break;
  }
}

function handleEscapeShortcut(event) {
  if (closeConfirmationDialog(false)) {
    event.preventDefault();
    return;
  }
  if (closeKeyboardShortcutsDialog()) {
    event.preventDefault();
    return;
  }
  if (closeDismissibleToasts()) {
    event.preventDefault();
    return;
  }
  if (closeOpenDropdownMenus()) {
    event.preventDefault();
    return;
  }
  if (blurShortcutInput(event.target)) {
    event.preventDefault();
  }
}

function closeDismissibleToasts() {
  let closed = false;
  document.querySelectorAll("[data-close-toast]").forEach((button) => {
    if (button instanceof HTMLElement) {
      closeToast(button);
      closed = true;
    }
  });
  document.querySelectorAll("[data-close-job-toast]").forEach((button) => {
    if (button instanceof HTMLButtonElement) {
      closeJobToast(button);
      closed = true;
    }
  });
  return closed;
}

function closeOpenDropdownMenus() {
  let closed = false;
  document.querySelectorAll(`${dropdownMenuSelector}[open]`).forEach((details) => {
    details.open = false;
    closed = true;
  });
  if (activePlaylistMenu) {
    closeActivePlaylistMenu();
    closed = true;
  }
  return closed;
}

function blurShortcutInput(target) {
  const editableTarget = shortcutInputTarget(target);
  if (!(editableTarget instanceof HTMLElement)) {
    return false;
  }
  editableTarget.blur();
  return true;
}

function confirmAction({
  title = "Confirm Action",
  message = "",
  confirmLabel = "Confirm",
  returnFocus = null,
} = {}) {
  if (
    !(confirmationDialog instanceof HTMLElement)
    || !(confirmationTitle instanceof HTMLElement)
    || !(confirmationMessage instanceof HTMLElement)
    || !(confirmationCancel instanceof HTMLButtonElement)
    || !(confirmationConfirm instanceof HTMLButtonElement)
    || typeof message !== "string"
    || !message.trim()
  ) {
    return Promise.resolve(false);
  }

  closeKeyboardShortcutsDialog({restoreFocus: false});
  closeOpenDropdownMenus();
  closeConfirmationDialog(false, {restoreFocus: false});

  confirmationTitle.textContent = title;
  confirmationMessage.textContent = message;
  confirmationConfirm.textContent = confirmLabel;
  confirmationReturnFocus = returnFocus instanceof HTMLElement
    ? returnFocus
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  confirmationDialog.hidden = false;
  try {
    confirmationCancel.focus({preventScroll: true});
  } catch {
    confirmationCancel.focus();
  }

  return new Promise((resolve) => {
    confirmationResolve = resolve;
  });
}

function confirmationDialogIsOpen() {
  return confirmationDialog instanceof HTMLElement && !confirmationDialog.hidden;
}

function closeConfirmationDialog(confirmed = false, options = {}) {
  if (
    !(confirmationDialog instanceof HTMLElement)
    || confirmationDialog.hidden
  ) {
    return false;
  }
  confirmationDialog.hidden = true;
  const resolve = confirmationResolve;
  confirmationResolve = null;
  const restoreFocus = options.restoreFocus !== false;
  if (
    restoreFocus
    && confirmationReturnFocus instanceof HTMLElement
    && confirmationReturnFocus.isConnected
  ) {
    try {
      confirmationReturnFocus.focus({preventScroll: true});
    } catch {
      confirmationReturnFocus.focus();
    }
  }
  confirmationReturnFocus = null;
  if (resolve) {
    resolve(Boolean(confirmed));
  }
  return true;
}

function showKeyboardShortcutsDialog(returnFocus = null) {
  if (!(keyboardShortcutsDialog instanceof HTMLElement)) {
    return;
  }
  if (keyboardShortcutsDialog.hidden) {
    keyboardShortcutsReturnFocus = returnFocus instanceof HTMLElement
      ? returnFocus
      : document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
  }
  keyboardShortcutsDialog.hidden = false;
  if (keyboardShortcutsClose instanceof HTMLElement) {
    try {
      keyboardShortcutsClose.focus({preventScroll: true});
    } catch {
      keyboardShortcutsClose.focus();
    }
  }
}

function keyboardShortcutsDialogIsOpen() {
  return keyboardShortcutsDialog instanceof HTMLElement && !keyboardShortcutsDialog.hidden;
}

function closeKeyboardShortcutsDialog(options = {}) {
  if (
    !(keyboardShortcutsDialog instanceof HTMLElement)
    || keyboardShortcutsDialog.hidden
  ) {
    return false;
  }
  keyboardShortcutsDialog.hidden = true;
  const restoreFocus = options.restoreFocus !== false;
  if (
    restoreFocus
    && keyboardShortcutsReturnFocus instanceof HTMLElement
    && keyboardShortcutsReturnFocus.isConnected
  ) {
    try {
      keyboardShortcutsReturnFocus.focus({preventScroll: true});
    } catch {
      keyboardShortcutsReturnFocus.focus();
    }
  }
  keyboardShortcutsReturnFocus = null;
  return true;
}

async function focusSearchShortcut() {
  const searchInput = searchShortcutInput();
  if (searchInput) {
    focusAndSelectSearchInput(searchInput);
    return;
  }
  await navigate("/albums");
  const nextSearchInput = searchShortcutInput();
  if (nextSearchInput) {
    focusAndSelectSearchInput(nextSearchInput);
  }
}

function searchShortcutInput() {
  const input = view.querySelector("[data-initial-focus], input[type='search']");
  return input instanceof HTMLInputElement ? input : null;
}

function focusSearchMenuInput(menu) {
  const input = menu.querySelector("input[type='search']");
  if (!(input instanceof HTMLInputElement)) {
    return;
  }
  requestAnimationFrame(() => {
    if (menu.open && input.isConnected) {
      focusAndSelectSearchInput(input, {openMenu: false});
    }
  });
}

function closeSearchMenu(form) {
  const menu = form.querySelector("details[data-search-menu]");
  if (menu instanceof HTMLDetailsElement) {
    menu.open = false;
  }
}

function focusAndSelectSearchInput(input, options = {}) {
  const menu = input.closest("details[data-search-menu]");
  if (options.openMenu !== false && menu instanceof HTMLDetailsElement) {
    menu.open = true;
  }
  try {
    input.focus({preventScroll: true});
  } catch {
    input.focus();
  }
  input.select();
}

function navigateToShortcutPage(path) {
  saveCurrentScrollState({anchor: null});
  navigate(path);
}

function isTextInputTarget(target) {
  return shortcutInputTarget(target) !== null;
}

function shortcutInputTarget(target) {
  if (!(target instanceof Element)) {
    return null;
  }
  return target.closest("input, textarea, select, audio, [contenteditable='true']");
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
  const durationIsIndeterminate = row.dataset.durationIsIndeterminate === "1";
  const durationSeconds = Number(row.dataset.durationSeconds);
  return {
    trackId,
    albumId: row.dataset.albumId || "",
    audioUrl: row.dataset.audioUrl || `/audio/${trackId}`,
    artUrl: row.dataset.artUrl || `/art/32/${trackId}`,
    title: row.dataset.title || `Track ${trackId}`,
    albumArtist: row.dataset.albumArtist || "",
    albumArtists: albumArtistsFromRow(row),
    album: row.dataset.album || "",
    durationSeconds: durationIsIndeterminate || !Number.isFinite(durationSeconds)
      ? null
      : durationSeconds,
    durationIsIndeterminate,
    fileType: row.dataset.fileType || "",
    audioMimeType: row.dataset.audioMimeType || "",
    audioCodec: row.dataset.audioCodec || "",
    unsupported: row.dataset.unsupported || "",
    unavailable: row.dataset.unavailable === "1"
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
    durationIsIndeterminate: false,
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
    void playExistingQueuePosition(Number(row.dataset.queuePosition) || 0);
    return;
  }
  const ids = allRows.slice(rowIndex).map((candidate) => Number(candidate.dataset.trackId)).filter(Number.isFinite);
  playQueue(ids, 0);
}

async function playQueue(trackIds, position, options = {}) {
  if (!trackIds.length) {
    return;
  }
  const requestedState = normalizeQueueState({
    track_ids: trackIds,
    position,
    loaded_track_id: trackIds[position],
    paused: false,
    errored_track_ids: options.preserveErrors ? queueState.errored_track_ids : [],
    unavailable_track_ids: []
  });
  submittedIndeterminatePlayKeys.clear();
  queueState = requestedState;
  const syncedState = await postQueue(requestedState);
  if (syncedState) {
    queueState = syncedState;
  }
  playQueuePosition(queueState.position);
}

async function playExistingQueuePosition(position) {
  if (position < 0 || position >= queueState.track_ids.length) {
    return;
  }
  const trackId = queueState.track_ids[position];
  if (!trackIsPlayable(trackId)) {
    return;
  }
  queueState = normalizeQueueState({
    ...queueState,
    position,
    loaded_track_id: trackId,
    paused: false
  });
  playQueuePosition(position);
}

function playQueuePosition(position) {
  if (position < 0 || position >= queueState.track_ids.length) {
    return;
  }
  if (position !== queueLoadedPosition()) {
    submittedIndeterminatePlayKeys.clear();
  }
  queueState.position = position;
  const trackId = queueState.track_ids[position];
  if (!trackIsPlayable(trackId)) {
    return;
  }
  queueState.loaded_track_id = trackId;
  playTrack(trackById(trackId), {restart: true});
}

function webAudioConstructor() {
  const candidate = (window && (window.AudioContext || window.webkitAudioContext))
    || globalThis.AudioContext
    || globalThis.webkitAudioContext;
  return typeof candidate === "function" ? candidate : null;
}

function webAudioCanPlayTrack(track) {
  return Boolean(
    track
    && webAudioConstructor()
    && !isIosSafari()
    && !trackDurationIsIndeterminate(track)
    && isSameOriginAudioUrl(track.audioUrl)
    && !unsupportedPlaybackMessage(track)
  );
}

function webAudioQueueSegment(startPosition) {
  const segment = [];
  for (let position = startPosition; position < queueState.track_ids.length; position += 1) {
    const trackId = queueState.track_ids[position];
    const track = trackById(trackId);
    if (!trackIsPlayable(trackId) || !webAudioCanPlayTrack(track)) {
      break;
    }
    segment.push({position, track});
  }
  return segment;
}

function fetchAudioBuffer(sourcePath, context) {
  const existing = audioBufferLoads.get(sourcePath);
  if (existing) {
    return existing;
  }
  const promise = fetch(sourcePath)
    .then((response) => {
      if (!response.ok) {
        throw new Error(`audio fetch failed: ${response.status}`);
      }
      return response.arrayBuffer();
    })
    .then((arrayBuffer) => decodeAudioBuffer(context, arrayBuffer));
  promise.catch(() => {
    audioBufferLoads.delete(sourcePath);
  });
  audioBufferLoads.set(sourcePath, promise);
  return promise;
}

function decodeAudioBuffer(context, arrayBuffer) {
  try {
    const decoded = context.decodeAudioData(arrayBuffer);
    if (decoded && typeof decoded.then === "function") {
      return decoded;
    }
  } catch {
    // Older WebKit implementations require success/error callbacks.
  }
  return new Promise((resolve, reject) => {
    context.decodeAudioData(arrayBuffer, resolve, reject);
  });
}

function audioBufferSilenceTrim(buffer) {
  const length = Number(buffer && buffer.length);
  const sampleRate = Number(buffer && buffer.sampleRate);
  const channels = Math.max(1, Number(buffer && buffer.numberOfChannels) || 1);
  if (
    !Number.isFinite(length)
    || length <= 0
    || !Number.isFinite(sampleRate)
    || sampleRate <= 0
    || !buffer
    || typeof buffer.getChannelData !== "function"
  ) {
    return {start: 0, end: 0};
  }
  const channelData = [];
  try {
    for (let channel = 0; channel < channels; channel += 1) {
      channelData.push(buffer.getChannelData(channel));
    }
  } catch {
    return {start: 0, end: 0};
  }
  const maxFrames = Math.min(length, Math.floor(webAudioMaxSilenceTrimSeconds * sampleRate));
  const minFrames = Math.floor(webAudioMinSilenceTrimSeconds * sampleRate);
  const frameIsSilent = (frame) => channelData.every((data) => (
    Math.abs(Number(data[frame]) || 0) <= webAudioSilenceTrimThreshold
  ));
  let startFrames = 0;
  for (; startFrames < maxFrames; startFrames += 1) {
    if (!frameIsSilent(startFrames)) {
      break;
    }
  }
  let endFrames = 0;
  for (; endFrames < maxFrames; endFrames += 1) {
    if (!frameIsSilent(length - endFrames - 1)) {
      break;
    }
  }
  if (startFrames < minFrames) {
    startFrames = 0;
  }
  if (endFrames < minFrames) {
    endFrames = 0;
  }
  if (startFrames + endFrames >= length) {
    return {start: 0, end: 0};
  }
  return {
    start: startFrames / sampleRate,
    end: endFrames / sampleRate
  };
}

function resumeAudioContext(context) {
  if (context && typeof context.resume === "function" && context.state === "suspended") {
    return context.resume();
  }
  return Promise.resolve();
}

function releaseDecodedAudioBuffers() {
  audioBufferLoads.clear();
}

function audioSourcePath(track) {
  const url = new URL(track.audioUrl, window.location.href);
  url.hash = "";
  return url.toString();
}

function isSameOriginAudioUrl(audioUrl) {
  try {
    return new URL(audioUrl, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

function isIosSafari() {
  const userAgent = navigator.userAgent || "";
  const platform = navigator.platform || "";
  const isIOS = /iP(?:ad|hone|od)/.test(userAgent)
    || (platform === "MacIntel" && Number(navigator.maxTouchPoints) > 1);
  return isIOS && browserName() === "Safari";
}

function queuePositionForTrack(trackId) {
  const loadedPosition = queueLoadedPosition();
  if (
    loadedPosition >= 0
    && loadedPosition < queueState.track_ids.length
    && queueState.track_ids[loadedPosition] === trackId
  ) {
    return loadedPosition;
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

function playbackEngineForTrack(track) {
  return webAudioPlaybackEngine.canPlayTrack(track) ? webAudioPlaybackEngine : nativeAudioEngine;
}

function setActivePlaybackEngine(engine) {
  if (!engine || activePlaybackEngine === engine) {
    applyActiveEngineVolume();
    return;
  }
  if (activePlaybackEngine) {
    activePlaybackEngine.clear({suppressCallbacks: true});
  }
  activePlaybackEngine = engine;
  applyActiveEngineVolume();
}

function clearPlaybackEngines() {
  pendingEngineStartKey = "";
  nativeAudioEngine.clear({suppressCallbacks: true});
  webAudioPlaybackEngine.clear({suppressCallbacks: true});
  activePlaybackEngine = nativeAudioEngine;
}

async function playTrack(track, options = {}) {
  if (!track) {
    return;
  }
  if (trackIsUnavailable(track.trackId)) {
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
  const engine = playbackEngineForTrack(track);
  setActivePlaybackEngine(engine);
  queueState.loaded_track_id = track.trackId;
  queueState.paused = false;
  updateNowPlaying(track);
  pendingEngineStartKey = engine === webAudioPlaybackEngine
    ? playbackStartKey(queueState.position, track.trackId)
    : "";
  const scrobbleOnPlayRequest = recordPlaybackStart(track);
  try {
    await engine.playTrack(track, {
      restart: Boolean(options.restart),
      position: queueState.position
    });
  } catch (err) {
    pendingEngineStartKey = "";
    if (mediaPlaybackWasAborted(err) && loadedTrackId() !== track.trackId) {
      return;
    }
    if (trackHasPlaybackError(track.trackId) && loadedTrackId() !== track.trackId) {
      return;
    }
    handlePlaybackFailure(track, playbackErrorMessage(track, err));
    return;
  }
  void scrobbleOnPlayRequest;
  updatePlaybackUi();
}

function mediaPlaybackWasAborted(error) {
  if (error && error.name === "AbortError") {
    return true;
  }
  const message = String(error && error.message ? error.message : "");
  return /aborted/i.test(message) && /media resource|fetching process|play\(\)/i.test(message);
}

function recordPlaybackStart(track) {
  const playbackPayload = {
    loaded_track_id: track.trackId,
    position: queueState.position,
    paused: false,
    errored_track_ids: queueState.errored_track_ids
  };
  const submitPlayedOnPlay = trackDurationIsIndeterminate(track);
  const request = postPlayback(playbackPayload)
    .then(() => {
      if (submitPlayedOnPlay) {
        return null;
      }
      return postScrobble(track.trackId, false);
    });
  if (submitPlayedOnPlay) {
    void request.then(() => submitIndeterminatePlayedScrobble(track));
  }
  return request;
}

function playbackStartKey(position, trackId) {
  return `${position}:${trackId}`;
}

async function submitIndeterminatePlayedScrobble(track) {
  if (!trackDurationIsIndeterminate(track)) {
    return;
  }
  const key = indeterminatePlayedScrobbleKey(track);
  if (submittedIndeterminatePlayKeys.has(key)) {
    return;
  }
  submittedIndeterminatePlayKeys.add(key);
  await postScrobble(track.trackId, true);
}

function indeterminatePlayedScrobbleKey(track) {
  const position = queueLoadedPosition();
  return `${position}:${track.trackId}`;
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
  clearPlaybackEngines();
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

function trackIsUnavailable(trackId) {
  const resolvedTrackId = Number(trackId);
  return Number.isFinite(resolvedTrackId)
    && queueState.unavailable_track_ids.includes(resolvedTrackId);
}

function trackIsPlayable(trackId) {
  return !trackIsUnavailable(trackId) && !trackHasPlaybackError(trackId);
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
    if (trackIsPlayable(queueState.track_ids[index])) {
      return index;
    }
  }
  return -1;
}

function previousPlayableQueuePosition(position) {
  for (let index = position - 1; index >= 0; index -= 1) {
    if (trackIsPlayable(queueState.track_ids[index])) {
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
      separator.textContent = ",\u00a0";
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
  const url = new URL("/albums", window.location.origin);
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
  const hasPlayableQueuedTrack = queueState.track_ids.some((trackId) => trackIsPlayable(trackId));
  updatePlayButton(playing);
  playButton.disabled = loadedId === null && !hasPlayableQueuedTrack;
  previousButton.disabled = !canMove(-1);
  nextButton.disabled = !canMove(1);

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
      row.classList.toggle("queue-error", trackHasPlaybackError(trackId) || trackIsUnavailable(trackId));
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
  updatePlaybackProgress();
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
  if (trackIsUnavailable(trackId)) {
    return "Unavailable";
  }
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

async function postQueueAppend(trackIds) {
  try {
    const response = await fetch("/api/queue/append", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({track_ids: trackIds})
    });
    if (!response.ok) {
      throw new Error(`queue append request failed: ${response.status}`);
    }
    return normalizeQueueState(await response.json());
  } catch {
    return null;
  }
}

async function postQueueRemove(position) {
  try {
    const response = await fetch("/api/queue/remove", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({position})
    });
    if (!response.ok) {
      throw new Error(`queue remove request failed: ${response.status}`);
    }
    const payload = await response.json();
    return {
      queue: normalizeQueueState(payload && payload.queue),
      playNext: Boolean(payload && payload.play_next),
      stopPlayback: Boolean(payload && payload.stop_playback)
    };
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
  if (!trackIds.length) {
    updatePlaybackUi();
    return;
  }
  const syncedState = await postQueueAppend(trackIds);
  if (syncedState) {
    queueState = syncedState;
  }
  updatePlaybackUi();
}

function clearLoadedPlayback() {
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  clearPlaybackEngines();
  updateNowPlaying(null);
}

function releaseAudioNetworkResources() {
  pageIsUnloading = true;
  manualPauseRequested = false;
  clearPendingPauseCommit();
  clearPauseStateSuppression();
  queueState.paused = true;
  try {
    clearPlaybackEngines();
    releaseDecodedAudioBuffers();
  } catch {
    return;
  }
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
  const previousLoadedId = loadedTrackId();
  const removal = await postQueueRemove(position);
  if (!removal) {
    return;
  }

  queueState = removal.queue;
  submittedIndeterminatePlayKeys.clear();

  if (removal.playNext) {
    playQueuePosition(queueState.position);
  } else if (removal.stopPlayback) {
    clearLoadedPlayback();
    updatePlaybackUi();
  } else {
    if (previousLoadedId !== loadedTrackId()) {
      clearLoadedPlayback();
    }
    updatePlaybackUi();
  }

  await refreshQueuePage();
}

async function postPlayback(payload) {
  try {
    const response = await fetch("/api/playback", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    if (!response.ok) {
      return null;
    }
    return normalizeQueueState(await response.json());
  } catch {
    return null;
  }
}

async function postScrobble(playbackId, submission) {
  const resolvedId = Number(playbackId);
  if (!Number.isFinite(resolvedId)) {
    return;
  }
  try {
    const response = await fetch("/api/scrobble", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        playback_id: resolvedId,
        submission: Boolean(submission),
        time: Date.now()
      })
    });
    if (response.ok) {
      await refreshHomePage();
    }
  } catch {
    return;
  }
}

async function refreshHomePage() {
  if (document.body.dataset.page !== "home") {
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
    ? ["cover", event.target.classList.contains("now-playing-cover") ? "now-playing-cover" : ""]
        .filter(Boolean)
        .join(" ")
    : "album-cover-placeholder";
  placeholder.setAttribute("aria-hidden", "true");
  event.target.replaceWith(placeholder);
}

view.addEventListener("error", replaceBrokenImage, true);
nowPlaying.addEventListener("error", replaceBrokenImage, true);
updateVolumeControl();
hydrateVisibleTracks();
updatePlaybackUi();
