(() => {
    "use strict";

    const player = document.querySelector("#player");
    if (!player) {
        return;
    }

    const audio = player.querySelector("[data-player-audio]");
    const titleElement = player.querySelector("[data-player-title]");
    const artistsElement = player.querySelector("[data-player-artists]");

    if (!audio || !titleElement || !artistsElement) {
        return;
    }

    let queue = [];
    let currentIndex = -1;
    let currentTrackID = null;

    function normalizedText(element) {
        if (!element) {
            return "";
        }

        return element.textContent
            .replace(/\s+/g, " ")
            .replace(/\s+,/g, ",")
            .trim();
    }

    function readTrack(button) {
        const row = button.closest(".track-row[data-track-id]");
        if (!row) {
            return null;
        }

        const id = button.dataset.trackId;
        const audioURL = button.dataset.audioUrl;

        if (!id || !audioURL) {
            return null;
        }

        return {
            id,
            audioURL,
            title:
                normalizedText(row.querySelector(".track-title")) ||
                "Unknown track",
            artists: normalizedText(
                row.querySelector(".track-artists"),
            ),
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

        artistsElement.textContent = track.artists;
        artistsElement.hidden = track.artists === "";
    }

    function updateActiveRows() {
        const rows = document.querySelectorAll(
            ".track-row[data-track-id]",
        );

        for (const row of rows) {
            const active =
                currentTrackID !== null &&
                row.dataset.trackId === currentTrackID;

            row.classList.toggle("is-active", active);

            if (active) {
                row.setAttribute("aria-current", "true");
            } else {
                row.removeAttribute("aria-current");
            }

            const button = row.querySelector(
                '[data-player-action="play"]',
            );

            if (button) {
                button.setAttribute(
                    "aria-pressed",
                    active ? "true" : "false",
                );
            }
        }
    }

    function requestPlayback() {
        const playback = audio.play();

        // It should be a promise, but a defensive check doesn't hurt
        if (playback && typeof playback.catch === "function") {
            playback.catch((error) => {
                // Changing tracks can abort an earlier play request.
                if (error.name !== "AbortError") {
                    console.warn("Unable to start playback", error);
                }
            });
        }
    }

    function selectTrack(index) {
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
        updateActiveRows();
        requestPlayback();
    }

    document.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }

        const button = event.target.closest(
            '[data-player-action="play"]',
        );

        if (!button) {
            return;
        }

        const queueElement = button.closest("[data-player-queue]");
        if (!queueElement) {
            return;
        }

        event.preventDefault();

        const snapshot = snapshotQueue(queueElement, button);

        if (
            snapshot.selectedIndex < 0 ||
            snapshot.tracks.length === 0
        ) {
            return;
        }

        queue = snapshot.tracks;
        selectTrack(snapshot.selectedIndex);
    });

    audio.addEventListener("ended", () => {
        const nextIndex = currentIndex + 1;

        if (nextIndex < queue.length) {
            selectTrack(nextIndex);
        }
    });
})();
