import {
    PLAYER_APPEND_QUEUE,
    PLAYER_NEXT,
    PLAYER_PLAYBACK_CHANGED,
    PLAYER_PREVIOUS,
    PLAYER_SEEK,
    PLAYER_SELECT_QUEUE,
    PLAYER_SET_VOLUME,
    PLAYER_STATE_REQUEST,
    PLAYER_TIME_CHANGED,
    PLAYER_TOGGLE,
    PLAYER_TOGGLE_MUTE,
    PLAYER_TRACK_CHANGED,
    PLAYER_VOLUME_CHANGED,
    PLAYER_QUEUE_CHANGED,
    PLAYER_SELECT_TRACK,
} from "./player-events.js";
import {
    loadPreferredVolume,
    savePreferredVolume,
} from "./player-preferences.js";

import { createScrobbler } from "./player-scrobble.js";
import { createRadioSession } from "./player-radio.js";
import { showToast } from "./notifications.js";

export default function registerPlayerEngine(up) {
    up.compiler("audio[data-player-audio]", (audio) =>
        playerEngineCompiler(up, audio),
    );
    up.compiler(".home-page", (page) => {
        const buttons = page.querySelectorAll("[data-radio-kind][data-radio-id]");
        const unbind = up.on(PLAYER_TRACK_CHANGED, (event) => {
            for (const button of buttons) {
                const active = button.dataset.radioKind === event.radioKind &&
                    button.dataset.radioId === event.radioEntityID;
                if (active) {
                    button.setAttribute("aria-current", "true");
                } else {
                    button.removeAttribute("aria-current");
                }
            }
        });
        up.emit(PLAYER_STATE_REQUEST);
        return unbind;
    });
}

function playerEngineCompiler(up, audio) {
    const preferredVolume = loadPreferredVolume();
    audio.volume = preferredVolume;

    const scrobbler = createScrobbler({
        url: audio.dataset.scrobbleUrl,
        onError: () => showToast("Unable to save playback activity.", { role: "alert" }),
    });
    let radio = null;
    let queue = [];
    let currentIndex = -1;
    let currentTrackID = null;
    let playbackRequested = false;
    let playRevision = 0;
    let retriedSource = false;
    let recoveryPosition = null;
    let sourceLoadedAt = null;
    let progressTimer = null;
    let dismissRecoveryToast = null;
    let recovering = false;

    // Remote redirects expire after one hour (audio.go). Refresh conservatively
    // on resume; the media element does not reliably expose the signed URL.
    const sourceRefreshAgeMs = 55 * 60 * 1000;
    const stallTimeoutMs = 5000;
    const recoveryTimeoutMs = 10000;
    let volumeBeforeMute = preferredVolume > 0 ? preferredVolume : 1;

    // Restart after this point; near the start, play the previous queue item.
    const restartThresholdSeconds = 3;

    const currentTrack = () => queue[currentIndex] || null;
    const hasTrack = () => currentTrack() !== null;
    const hasPrevious = () => hasTrack() && !radio?.pending &&
        (radio?.current ? BigInt(radio.current.state.current_position) > 0n : currentIndex > 0);
    const hasNext = () => hasTrack() && !radio?.pending &&
        (radio?.current ? true : currentIndex < queue.length - 1);

    // Unpoly updates and restores this metadata with the current page or layer.
    const pageTitlePrefix = () => {
        const title = document.querySelector('meta[name="kukicha-page-title"]')?.content;
        return title ? `${title} - ` : "";
    };
    const updateDocumentTitle = () => {
        const track = currentTrack();
        const suffix = track && !audio.paused && !audio.ended
            ? `Now playing "${track.title}"`
            : "Kukicha";
        const title = pageTitlePrefix() + suffix;
        if (document.title !== title) document.title = title;
    };
    // Unpoly can restore page or overlay titles independently of playback.
    const titleObserver = new MutationObserver(updateDocumentTitle);
    titleObserver.observe(document.head, {
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["content"],
        subtree: true,
    });
    updateDocumentTitle();

    const emitTrack = () => {
        updateDocumentTitle();
        up.emit(PLAYER_TRACK_CHANGED, {
            track: currentTrack(),
            trackId: currentTrackID,
            currentIndex,
            hasTrack: hasTrack(),
            hasPrevious: hasPrevious(),
            hasNext: hasNext(),
            radioName: radio?.current ? `${radio.current.name} radio` : "",
            radioKind: radio?.current?.kind ?? null,
            radioEntityID: radio?.current?.entity_id ?? null,
            radioMode: !!radio?.current,
            pending: !!radio?.pending,
        });
    };
    const emitPlayback = () => {
        updateDocumentTitle();
        up.emit(PLAYER_PLAYBACK_CHANGED, {
            trackId: currentTrackID,
            currentIndex,
            hasTrack: hasTrack(),
            hasPrevious: hasPrevious(),
            hasNext: hasNext(),
            radioName: radio?.current ? `${radio.current.name} radio` : "",
            radioMode: !!radio?.current,
            pending: !!radio?.pending,
            playing: hasTrack() && playbackRequested,
        });
    };
    const emitTime = () => {
        up.emit(PLAYER_TIME_CHANGED, {
            currentTime: Number.isFinite(audio.currentTime)
                ? audio.currentTime
                : 0,
            duration: Number.isFinite(audio.duration) ? audio.duration : 0,
            hasTrack: hasTrack(),
        });
    };
    const emitVolume = () => {
        up.emit(PLAYER_VOLUME_CHANGED, {
            muted: audio.muted,
            volume: audio.volume,
        });
    };
    const emitQueue = () => {
        up.emit(PLAYER_QUEUE_CHANGED, {
            tracks: radio?.current ? [] : queue,
        });
    };
    const emitState = () => {
        emitQueue();
        emitTrack();
        emitPlayback();
        emitTime();
        emitVolume();
    };

    const clearRecoveryFeedback = () => {
        dismissRecoveryToast?.();
        dismissRecoveryToast = null;
        recovering = false;
    };
    const stopProgressWatch = () => {
        clearInterval(progressTimer);
        progressTimer = null;
    };
    const pausePlayback = () => {
        playbackRequested = false;
        ++playRevision;
        stopProgressWatch();
        clearRecoveryFeedback();
        audio.pause();
        emitPlayback();
    };
    const loadSource = () => {
        sourceLoadedAt = Date.now();
        audio.load();
    };
    const reportPlaybackFailure = (reason) => {
        if (!playbackRequested || !hasTrack()) return;
        pausePlayback();
        const title = currentTrack().title;
        showToast(`Couldn’t play ${title ? `“${title}”` : "this track"}. ${reason}`, { role: "alert" });
    };

    const watchProgress = () => {
        stopProgressWatch();
        let position = audio.currentTime;
        let lastProgressAt = Date.now();
        progressTimer = setInterval(() => {
            if (!playbackRequested || !hasTrack()) {
                stopProgressWatch();
                return;
            }
            const now = Date.now();
            // A playing event alone is not proof that audio is advancing.
            if (!audio.paused && !audio.seeking && audio.readyState >= 3 &&
                audio.currentTime > position) {
                lastProgressAt = now;
                if (recovering) {
                    clearRecoveryFeedback();
                    showToast("Playback resumed.");
                }
            }
            position = audio.currentTime;
            if (now - lastProgressAt < (retriedSource ? recoveryTimeoutMs : stallTimeoutMs)) return;
            if (retriedSource) {
                reportPlaybackFailure("The audio is taking too long to load. Press Play to try again.");
            } else {
                reloadForPlayback("Playback stalled. Reconnecting…");
            }
        }, 1000);
    };

    const unsupportedSource = "The file may be protected, unavailable, or in an unsupported format.";
    const playSource = async () => {
        const revision = ++playRevision;
        try {
            await audio.play();
        } catch (error) {
            if (revision !== playRevision) return;
            // The media error event owns source failures, including their retry.
            if (!audio.error && error.name !== "AbortError") {
                if (error.name === "NotAllowedError") {
                    reportPlaybackFailure("The browser blocked playback. Press Play to try again.");
                } else if (error.name === "NotSupportedError") {
                    reportPlaybackFailure(unsupportedSource);
                } else {
                    reportPlaybackFailure("Playback could not start. Please try again.");
                }
            }
            emitPlayback();
        }
    };

    const reloadForPlayback = (message) => {
        retriedSource = true;
        ++playRevision;
        // Keep the original position if another load has not reached metadata yet.
        recoveryPosition ??= Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
        clearRecoveryFeedback();
        recovering = true;
        dismissRecoveryToast = showToast(message);
        // src still points to our no-store application endpoint. Calling play
        // immediately also preserves the user gesture on an explicit resume.
        loadSource();
        watchProgress();
        playSource();
        emitPlayback();
    };

    const requestPlayback = () => {
        playbackRequested = true;
        retriedSource = false;
        if (audio.error || (sourceLoadedAt !== null &&
            Date.now() - sourceLoadedAt >= sourceRefreshAgeMs)) {
            reloadForPlayback("Refreshing the audio connection. Resuming from your paused position…");
            return;
        }
        watchProgress();
        playSource();
        emitPlayback();
    };

    const recoverSource = () => {
        // Media errors expose neither HTTP status nor reliably the redirect URL.
        // Retry once per Play request, shared with the stall watchdog.
        if (!hasTrack() || !audio.error || !playbackRequested) return;
        const code = audio.error.code;
        if (retriedSource || ![2, 4].includes(code)) {
            if (code === 2) reportPlaybackFailure("The audio download was interrupted.");
            else if (code === 3) reportPlaybackFailure("The browser couldn’t decode the audio. The file may be damaged or unsupported.");
            else if (code === 4) reportPlaybackFailure(unsupportedSource);
            else reportPlaybackFailure("The browser reported a media error.");
            return;
        }
        reloadForPlayback("The audio connection was interrupted. Reconnecting…");
    };

    const loadedMetadata = () => {
        if (recoveryPosition !== null) {
            const position = recoveryPosition;
            recoveryPosition = null;
            audio.currentTime = Number.isFinite(audio.duration)
                ? Math.min(position, audio.duration) : position;
        }
        emitTime();
    };

    const selectTrack = (index, autoplay = true) => {
        if (index < 0 || index >= queue.length) {
            return;
        }

        const track = queue[index];
        const sameSource =
            currentTrackID === track.id &&
            audio.getAttribute("src") === track.audioURL;

        const differentEntry = index !== currentIndex;
        if (!sameSource || differentEntry) scrobbler.select();
        currentIndex = index;
        currentTrackID = track.id;

        if (!sameSource) {
            pausePlayback();
            retriedSource = false;
            recoveryPosition = null;
            audio.setAttribute("src", track.audioURL);
            loadSource();
        } else if (differentEntry) {
            recoveryPosition = null;
            audio.currentTime = 0;
        }

        emitTrack();
        emitTime();
        if (autoplay) requestPlayback();
        emitPlayback();
    };

    const togglePlayback = () => {
        if (radio?.pending) return;
        if (!hasTrack()) {
            if (queue.length) selectTrack(0);
            return;
        }

        // load() temporarily pauses the element during recovery; user intent
        // keeps the button acting as Pause until playback is cancelled.
        if (playbackRequested) pausePlayback();
        else requestPlayback();
    };

    const selectTrackInQueue = (event) => {
        if (radio?.current || radio?.pending) return;
        const selectedIndex = Number(event.selectedIndex);

        if (
            !Number.isInteger(selectedIndex) ||
            selectedIndex < 0 ||
            selectedIndex >= queue.length
        ) {
            return;
        }

        if (selectedIndex === currentIndex) {
            togglePlayback();
            return;
        }

        selectTrack(selectedIndex);
    };

    const selectQueue = (event) => {
        if (
            !Array.isArray(event.tracks) ||
            event.selectedIndex < 0 ||
            event.selectedIndex >= event.tracks.length
        ) {
            return;
        }

        const selectedTrack = event.tracks[event.selectedIndex];
        // The active album row controls the current track, including in radio mode.
        if (!event.startAlbum && selectedTrack.id === currentTrackID) {
            togglePlayback();
            return;
        }

        const leavingRadio = !!radio?.current || !!radio?.pending;
        radio?.leave();
        if (event.startAlbum || leavingRadio) currentIndex = -1;
        queue = [...event.tracks];
        emitQueue();
        selectTrack(event.selectedIndex);
        if (event.startAlbum || leavingRadio) audio.currentTime = 0;
    };

    const playPrevious = () => {
        if (radio?.pending) return;
        if (radio?.current) { radio.move(-1); return; }
        if (audio.currentTime > restartThresholdSeconds || !hasPrevious()) {
            audio.currentTime = 0;
            emitTime();
            return;
        }

        selectTrack(currentIndex - 1);
    };

    const playNext = () => {
        if (radio?.pending) return;
        if (radio?.current) { radio.move(1); return; }
        selectTrack(currentIndex + 1);
    };

    const seek = (event) => {
        if (!Number.isFinite(audio.duration) || audio.duration <= 0) {
            return;
        }

        audio.currentTime = Number(event.value);
        // optimistically do an immediate UI update to reflect new `currentTime`
        // `timeupdate` fires again shortly which is a harmless UI update
        emitTime();
    };

    const setVolume = (event) => {
        const volume = Number(event.value);
        if (!Number.isFinite(volume) || volume < 0 || volume > 1) {
            return;
        }

        audio.muted = false;
        audio.volume = volume;
        savePreferredVolume(volume);
        if (volume > 0) {
            volumeBeforeMute = volume;
        }
    };

    const toggleMute = () => {
        if (audio.muted || audio.volume === 0) {
            audio.muted = false;
            audio.volume = volumeBeforeMute > 0 ? volumeBeforeMute : 1;
            savePreferredVolume(audio.volume);
        } else {
            volumeBeforeMute = audio.volume;
            audio.muted = true;
        }
    };

    let storage;
    try { storage = window.sessionStorage; } catch { /* In-memory playback still works. */ }
    radio = createRadioSession({
        url: audio.dataset.radioUrl,
        storage,
        onStatus: emitState,
        onSelection: (track, autoplay) => {
            queue = [track];
            currentIndex = -1;
            emitQueue();
            selectTrack(0, autoplay);
        },
        onError: (error, unavailable) => {
            if (unavailable) {
                pausePlayback();
                recoveryPosition = null;
                audio.removeAttribute("src");
                audio.load();
                queue = [];
                currentIndex = -1;
                currentTrackID = null;
                scrobbler.select();
            }
            showToast(error.message, { role: "alert" });
        },
    });
    const radioClick = (event) => {
        const button = event.target instanceof Element
            ? event.target.closest("[data-radio-kind][data-radio-id]") : null;
        if (!button || button.disabled) return;
        event.preventDefault();
        radio.start(button.dataset.radioKind, button.dataset.radioId);
    };

    const unbind = [
        () => {
            titleObserver.disconnect();
            document.title = pageTitlePrefix() + "Kukicha";
        },
        () => {
            playbackRequested = false;
            ++playRevision;
            stopProgressWatch();
            clearRecoveryFeedback();
        },
        up.on(document, "click", radioClick),
        () => radio.dispose(),
        () => scrobbler.dispose(),
        up.on(PLAYER_SELECT_QUEUE, selectQueue),
        up.on(PLAYER_APPEND_QUEUE, (event) => {
            if (!Array.isArray(event.tracks) || !event.tracks.length) return;
            const leavingRadio = !!radio.current || radio.pending;
            radio.leave();
            if (leavingRadio) {
                pausePlayback();
                recoveryPosition = null;
                queue = [];
                currentIndex = -1;
            }
            queue = [...queue, ...event.tracks];
            emitQueue();
            if (!hasTrack()) selectTrack(0, false);
            emitPlayback();
        }),
        up.on(PLAYER_TOGGLE, togglePlayback),
        up.on(PLAYER_PREVIOUS, playPrevious),
        up.on(PLAYER_NEXT, playNext),
        up.on(PLAYER_SEEK, seek),
        up.on(PLAYER_SET_VOLUME, setVolume),
        up.on(PLAYER_TOGGLE_MUTE, toggleMute),
        up.on(PLAYER_STATE_REQUEST, emitState),
        up.on(PLAYER_SELECT_TRACK, selectTrackInQueue),
        up.on(audio, "loadedmetadata", loadedMetadata),
        up.on(audio, "error", recoverSource),
        up.on(audio, "durationchange", emitTime),
        up.on(audio, "timeupdate", emitTime),
        up.on(audio, "play", emitPlayback),
        up.on(audio, "playing", () => {
            if (hasTrack() && !audio.paused && !audio.ended) scrobbler.start(currentTrackID);
        }),
        up.on(audio, "pause", emitPlayback),
        up.on(audio, "volumechange", emitVolume),
        up.on(audio, "ended", () => {
            playbackRequested = false;
            stopProgressWatch();
            clearRecoveryFeedback();
            scrobbler.finish();
            if (hasNext()) {
                playNext();
            } else {
                playbackRequested = false;
                emitPlayback();
            }
        }),
    ];

    radio.restore();
    return unbind;
}
