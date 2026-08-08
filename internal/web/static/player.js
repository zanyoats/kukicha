(() => {
    "use strict";

    const player = document.querySelector("#player");
    if (!player) {
        return;
    }

    const audio = player.querySelector("[data-player-audio]");
    const albumElement = player.querySelector("[data-player-album]");
    const albumArtistsElement = player.querySelector(
        "[data-player-album-artists]",
    );
    const artworkElement = player.querySelector("[data-player-artwork]");
    const titleElement = player.querySelector("[data-player-title]");
    const currentTimeElement = player.querySelector(
        "[data-player-current-time]",
    );
    const durationElement = player.querySelector("[data-player-duration]");
    const seekControl = player.querySelector("[data-player-seek]");
    const volumeControl = player.querySelector("[data-player-volume]");
    const playButton = player.querySelector('[data-player-action="toggle"]');
    const previousButton = player.querySelector(
        '[data-player-action="previous"]',
    );
    const nextButton = player.querySelector('[data-player-action="next"]');
    const muteButton = player.querySelector('[data-player-action="mute"]');
    const playIcon = player.querySelector("[data-player-play-icon]");
    const volumeIcon = player.querySelector("[data-player-volume-icon]");

    if (
        !audio ||
        !albumElement ||
        !albumArtistsElement ||
        !artworkElement ||
        !titleElement ||
        !currentTimeElement ||
        !durationElement ||
        !seekControl ||
        !volumeControl ||
        !playButton ||
        !previousButton ||
        !nextButton ||
        !muteButton ||
        !playIcon ||
        !volumeIcon
    ) {
        return;
    }

    let queue = [];
    let currentIndex = -1;
    let currentTrackID = null;
    let volumeBeforeMute = 1;

    function normalizedText(element) {
        if (!element) {
            return "";
        }

        return element.textContent
            .replace(/\s+/g, " ")
            .replace(/\s+,/g, ",")
            .trim();
    }

    function setIcon(icon, name) {
        icon.setAttribute("href", `#icon-${name}`);
    }

    function setRangeProgress(control, value, maximum) {
        const progress =
            Number.isFinite(value) && Number.isFinite(maximum) && maximum > 0
                ? Math.min(100, Math.max(0, (value / maximum) * 100))
                : 0;

        control.style.setProperty("--range-progress", `${progress}%`);
    }

    function formatTime(value) {
        if (!Number.isFinite(value) || value < 0) {
            return "0:00";
        }

        const totalSeconds = Math.floor(value);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = String(totalSeconds % 60).padStart(2, "0");

        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`;
        }

        return `${minutes}:${seconds}`;
    }

    function readTrack(button) {
        const row = button.closest(".track-row[data-track-id]");
        const albumPage = button.closest(".album-page");
        if (!row || !albumPage) {
            return null;
        }

        const id = button.dataset.trackId;
        const audioURL = button.dataset.audioUrl;
        const albumArtwork = albumPage.querySelector(
            "[data-player-album-artwork]",
        );

        if (!id || !audioURL) {
            return null;
        }

        return {
            id,
            audioURL,
            title:
                normalizedText(row.querySelector(".track-title")) ||
                "Unknown track",
            artists: normalizedText(row.querySelector(".track-artists")),
            album:
                normalizedText(
                    albumPage.querySelector("[data-player-album-title]"),
                ) || "Unknown album",
            albumArtists: normalizedText(
                albumPage.querySelector(
                    "[data-player-album-artists-source]",
                ),
            ),
            artworkURL: albumArtwork ? albumArtwork.currentSrc : "",
        };
    }

    function snapshotQueue(queueElement, selectedButton) {
        const snapshot = [];
        let selectedIndex = -1;

        const buttons = queueElement.querySelectorAll(
            '[data-player-action="play"]',
        );

        for (const button of buttons) {
            const track = readTrack(button);
            if (!track) {
                continue;
            }

            if (button === selectedButton) {
                selectedIndex = snapshot.length;
            }

            snapshot.push(track);
        }

        return {
            tracks: snapshot,
            selectedIndex,
        };
    }

    function updateNowPlaying(track) {
        titleElement.textContent = track.title;
        titleElement.title = track.title;
        albumElement.textContent = track.album;
        albumElement.title = track.album;

        albumArtistsElement.textContent = track.albumArtists;
        albumArtistsElement.title = track.albumArtists;
        albumArtistsElement.hidden = track.albumArtists === "";

        if (track.artworkURL) {
            artworkElement.src = track.artworkURL;
            artworkElement.alt = `Artwork for ${track.album}`;
            artworkElement.hidden = false;
        } else {
            artworkElement.removeAttribute("src");
            artworkElement.alt = "";
            artworkElement.hidden = true;
        }
    }

    function updateActiveRows() {
        const rows = document.querySelectorAll(
            ".track-row[data-track-id]",
        );

        for (const row of rows) {
            const active =
                currentTrackID !== null &&
                row.dataset.trackId === currentTrackID;
            const playing = active && !audio.paused && !audio.ended;

            row.classList.toggle("is-active", active);

            if (active) {
                row.setAttribute("aria-current", "true");
            } else {
                row.removeAttribute("aria-current");
            }

            const button = row.querySelector(
                '[data-player-action="play"]',
            );
            const icon = row.querySelector("[data-track-play-icon]");

            if (button) {
                button.setAttribute("aria-pressed", playing ? "true" : "false");
                button.setAttribute(
                    "aria-label",
                    `${playing ? "Pause" : "Play"} ${
                        normalizedText(row.querySelector(".track-title")) ||
                        "track"
                    }`,
                );
            }

            if (icon) {
                setIcon(icon, playing ? "pause" : "play");
            }
        }
    }

    function updateTransportControls() {
        const hasTrack = currentIndex >= 0 && currentIndex < queue.length;
        const playing = hasTrack && !audio.paused && !audio.ended;

        playButton.disabled = !hasTrack;
        playButton.setAttribute("aria-label", playing ? "Pause" : "Play");
        setIcon(playIcon, playing ? "pause" : "play");

        previousButton.disabled = !hasTrack || currentIndex <= 0;
        nextButton.disabled = !hasTrack || currentIndex >= queue.length - 1;

        updateActiveRows();
    }

    function updateTimeline() {
        const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
        const currentTime = Number.isFinite(audio.currentTime)
            ? audio.currentTime
            : 0;

        seekControl.max = String(duration);
        seekControl.value = String(Math.min(currentTime, duration));
        seekControl.disabled = duration <= 0;
        currentTimeElement.textContent = formatTime(currentTime);
        durationElement.textContent = formatTime(duration);
        setRangeProgress(seekControl, currentTime, duration);
    }

    function updateVolumeControls() {
        const effectiveVolume = audio.muted ? 0 : audio.volume;

        volumeControl.value = String(effectiveVolume);
        setRangeProgress(volumeControl, effectiveVolume, 1);
        muteButton.setAttribute(
            "aria-label",
            effectiveVolume === 0 ? "Unmute" : "Mute",
        );
        setIcon(volumeIcon, effectiveVolume === 0 ? "volume-off" : "volume");
    }

    function requestPlayback() {
        const playback = audio.play();

        if (playback && typeof playback.catch === "function") {
            playback.catch((error) => {
                if (error.name !== "AbortError") {
                    console.warn("Unable to start playback", error);
                }
            });
        }
    }

    function selectTrack(index, autoplay = true) {
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

        updateNowPlaying(track);
        updateTimeline();
        updateTransportControls();

        if (autoplay) {
            requestPlayback();
        }
    }

    function togglePlayback() {
        if (currentIndex < 0 || currentIndex >= queue.length) {
            return;
        }

        if (audio.paused || audio.ended) {
            requestPlayback();
        } else {
            audio.pause();
        }
    }

    document.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }

        const rowPlayButton = event.target.closest(
            '[data-player-action="play"]',
        );

        if (rowPlayButton) {
            const queueElement = rowPlayButton.closest("[data-player-queue]");
            if (!queueElement) {
                return;
            }

            event.preventDefault();

            const selectedTrackID = rowPlayButton.dataset.trackId;
            if (selectedTrackID === currentTrackID) {
                togglePlayback();
                return;
            }

            const snapshot = snapshotQueue(queueElement, rowPlayButton);
            if (
                snapshot.selectedIndex < 0 ||
                snapshot.tracks.length === 0
            ) {
                return;
            }

            queue = snapshot.tracks;
            selectTrack(snapshot.selectedIndex);
            return;
        }

        const control = event.target.closest("[data-player-action]");
        if (!control || !player.contains(control)) {
            return;
        }

        switch (control.dataset.playerAction) {
        case "toggle":
            togglePlayback();
            break;

        case "previous":
            if (audio.currentTime > 3) {
                audio.currentTime = 0;
                updateTimeline();
            } else {
                selectTrack(currentIndex - 1);
            }
            break;

        case "next":
            selectTrack(currentIndex + 1);
            break;

        case "mute":
            if (audio.muted || audio.volume === 0) {
                audio.muted = false;
                audio.volume = volumeBeforeMute > 0 ? volumeBeforeMute : 1;
            } else {
                volumeBeforeMute = audio.volume;
                audio.muted = true;
            }
            updateVolumeControls();
            break;
        }
    });

    seekControl.addEventListener("input", () => {
        if (!Number.isFinite(audio.duration) || audio.duration <= 0) {
            return;
        }

        audio.currentTime = Number(seekControl.value);
        updateTimeline();
    });

    volumeControl.addEventListener("input", () => {
        const volume = Number(volumeControl.value);

        audio.muted = false;
        audio.volume = volume;
        if (volume > 0) {
            volumeBeforeMute = volume;
        }

        updateVolumeControls();
    });

    audio.addEventListener("loadedmetadata", updateTimeline);
    audio.addEventListener("durationchange", updateTimeline);
    audio.addEventListener("timeupdate", updateTimeline);
    audio.addEventListener("play", updateTransportControls);
    audio.addEventListener("pause", updateTransportControls);
    audio.addEventListener("volumechange", updateVolumeControls);
    audio.addEventListener("ended", () => {
        const nextIndex = currentIndex + 1;

        if (nextIndex < queue.length) {
            selectTrack(nextIndex);
        } else {
            updateTransportControls();
        }
    });

    updateVolumeControls();
    updateTimeline();
    updateTransportControls();

    if (window.up) {
        window.up.history.config.restoreTargets = ["[up-main]"];

        window.up.on("up:fragment:inserted", () => {
            updateActiveRows();
        });
    }
})();
