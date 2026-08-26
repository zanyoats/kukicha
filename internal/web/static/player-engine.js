import {
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
} from "./player-events.js";
import {
    loadPreferredVolume,
    savePreferredVolume,
} from "./player-preferences.js";

export default function registerPlayerEngine(up) {
    up.compiler("audio[data-player-audio]", (audio) =>
        playerEngineCompiler(up, audio),
    );
}

function playerEngineCompiler(up, audio) {
    const preferredVolume = loadPreferredVolume();
    audio.volume = preferredVolume;

    let queue = [];
    let currentIndex = -1;
    let currentTrackID = null;
    let volumeBeforeMute = preferredVolume > 0 ? preferredVolume : 1;

    // Restart after this point; near the start, play the previous queue item.
    const restartThresholdSeconds = 3;

    const currentTrack = () => queue[currentIndex] || null;
    const hasTrack = () => currentTrack() !== null;
    const hasPrevious = () => hasTrack() && currentIndex > 0;
    const hasNext = () => hasTrack() && currentIndex < queue.length - 1;

    const emitTrack = () => {
        up.emit(PLAYER_TRACK_CHANGED, {
            track: currentTrack(),
            trackId: currentTrackID,
            hasTrack: hasTrack(),
            hasPrevious: hasPrevious(),
            hasNext: hasNext(),
        });
    };
    const emitPlayback = () => {
        up.emit(PLAYER_PLAYBACK_CHANGED, {
            trackId: currentTrackID,
            hasTrack: hasTrack(),
            hasPrevious: hasPrevious(),
            hasNext: hasNext(),
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
    const emitState = () => {
        emitTrack();
        emitPlayback();
        emitTime();
        emitVolume();
    };

    const requestPlayback = async () => {
        try {
            await audio.play();
        } catch (error) {
            if (error.name !== "AbortError") {
                console.warn("Unable to start playback", error);
            }
            emitPlayback();
        }
    };

    const selectTrack = (index) => {
        if (index < 0 || index >= queue.length) {
            return;
        }

        const track = queue[index];
        const sameSource =
            currentTrackID === track.id &&
            audio.getAttribute("src") === track.audioURL;

        currentIndex = index;
        currentTrackID = track.id;

        if (!sameSource) {
            audio.setAttribute("src", track.audioURL);
            audio.load();
        }

        emitTrack();
        emitTime();
        requestPlayback();
    };

    const togglePlayback = () => {
        if (!hasTrack()) {
            return;
        }

        if (audio.paused || audio.ended) {
            requestPlayback();
        } else {
            audio.pause();
        }
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
        if (selectedTrack.id === currentTrackID) {
            togglePlayback();
            return;
        }

        queue = event.tracks;
        selectTrack(event.selectedIndex);
    };

    const playPrevious = () => {
        if (audio.currentTime > restartThresholdSeconds || !hasPrevious()) {
            audio.currentTime = 0;
            emitTime();
            return;
        }

        selectTrack(currentIndex - 1);
    };

    const playNext = () => selectTrack(currentIndex + 1);

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

    const unbind = [
        up.on(PLAYER_SELECT_QUEUE, selectQueue),
        up.on(PLAYER_TOGGLE, togglePlayback),
        up.on(PLAYER_PREVIOUS, playPrevious),
        up.on(PLAYER_NEXT, playNext),
        up.on(PLAYER_SEEK, seek),
        up.on(PLAYER_SET_VOLUME, setVolume),
        up.on(PLAYER_TOGGLE_MUTE, toggleMute),
        up.on(PLAYER_STATE_REQUEST, emitState),
        up.on(audio, "loadedmetadata", emitTime),
        up.on(audio, "durationchange", emitTime),
        up.on(audio, "timeupdate", emitTime),
        up.on(audio, "play", emitPlayback),
        up.on(audio, "pause", emitPlayback),
        up.on(audio, "volumechange", emitVolume),
        up.on(audio, "ended", () => {
            if (hasNext()) {
                selectTrack(currentIndex + 1);
            } else {
                emitPlayback();
            }
        }),
    ];

    return unbind;
}
