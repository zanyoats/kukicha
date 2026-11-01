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

    let radio = null;
    let queue = [];
    let currentIndex = -1;
    let currentTrackID = null;
    let playbackRequested = false;
    let retriedSource = false;
    let recoveryPosition = null;
    let volumeBeforeMute = preferredVolume > 0 ? preferredVolume : 1;

    // Restart after this point; near the start, play the previous queue item.
    const restartThresholdSeconds = 3;

    const currentTrack = () => queue[currentIndex] || null;
    const hasTrack = () => currentTrack() !== null;
    const hasPrevious = () => hasTrack() && !radio?.pending &&
        (radio?.current ? BigInt(radio.current.state.current_position) > 0n : currentIndex > 0);
    const hasNext = () => hasTrack() && !radio?.pending &&
        (radio?.current ? true : currentIndex < queue.length - 1);

    const emitTrack = () => {
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
        up.emit(PLAYER_PLAYBACK_CHANGED, {
            trackId: currentTrackID,
            currentIndex,
            hasTrack: hasTrack(),
            hasPrevious: hasPrevious(),
            hasNext: hasNext(),
            radioName: radio?.current ? `${radio.current.name} radio` : "",
            radioMode: !!radio?.current,
            pending: !!radio?.pending,
            playing: hasTrack() && !audio.paused && !audio.ended,
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

    const requestPlayback = async () => {
        playbackRequested = true;
        try {
            await audio.play();
        } catch (error) {
            if (error.name !== "AbortError") {
                console.warn("Unable to start playback", error);
            }
            emitPlayback();
        }
    };

    const recoverSource = () => {
        // Media errors do not expose HTTP status codes or reliably expose the
        // redirected URL. Reload once to recover expired remote signatures,
        // without looping on a missing object or an unsupported format.
        if (!hasTrack() || retriedSource || !audio.error ||
            ![2, 4].includes(audio.error.code)) {
            emitPlayback();
            return;
        }

        retriedSource = true;
        recoveryPosition = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
        // src still points to our application endpoint, not the redirect URL.
        audio.load();
    };

    const loadedMetadata = () => {
        if (recoveryPosition !== null) {
            const position = recoveryPosition;
            recoveryPosition = null;
            audio.currentTime = Number.isFinite(audio.duration)
                ? Math.min(position, audio.duration) : position;
            if (playbackRequested) {
                requestPlayback();
            }
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
        currentIndex = index;
        currentTrackID = track.id;

        if (!sameSource) {
            retriedSource = false;
            recoveryPosition = null;
            audio.setAttribute("src", track.audioURL);
            audio.load();
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

        if (audio.paused || audio.ended) {
            requestPlayback();
        } else {
            playbackRequested = false;
            audio.pause();
        }
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
        onUnauthorized: (url) => window.location.assign(url),
        onSelection: (track, autoplay) => {
            queue = [track];
            currentIndex = -1;
            emitQueue();
            selectTrack(0, autoplay);
        },
        onError: (error, unavailable) => {
            if (unavailable) {
                playbackRequested = false;
                audio.pause();
                audio.removeAttribute("src");
                audio.load();
                queue = [];
                currentIndex = -1;
                currentTrackID = null;
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
        up.on(document, "click", radioClick),
        () => radio.dispose(),
        up.on(PLAYER_SELECT_QUEUE, selectQueue),
        up.on(PLAYER_APPEND_QUEUE, (event) => {
            if (!Array.isArray(event.tracks) || !event.tracks.length) return;
            const leavingRadio = !!radio.current || radio.pending;
            radio.leave();
            if (leavingRadio) {
                playbackRequested = false;
                audio.pause();
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
        up.on(audio, "pause", emitPlayback),
        up.on(audio, "volumechange", emitVolume),
        up.on(audio, "ended", () => {
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
