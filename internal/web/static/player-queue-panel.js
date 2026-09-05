import {
    PLAYER_PLAYBACK_CHANGED,
    PLAYER_QUEUE_CHANGED,
    PLAYER_STATE_REQUEST,
    PLAYER_TRACK_CHANGED,
    PLAYER_SELECT_TRACK,
} from "./player-events.js";
import { formatTime, setIcon } from "./player-utils.js";

export default function registerPlayerQueuePanel(up) {
    // Each opening creates a new drawer from the template and adds it to the DOM.
    // CSS places it above the footer and hides anything below that edge.
    up.animation("player-queue-from-footer", (element, options) => {
        // Start out of view by moving the drawer down by its own height.
        element.style.transform = "translateY(100%)";
        // Remove that offset gradually to slide it up into its normal position.
        return up.animate(element, { transform: "none" }, options);
    });

    // Slide the drawer down out of view. Unpoly removes it from the DOM afterward;
    // the template stays on the page for the next opening.
    up.animation("player-queue-to-footer", (element, options) =>
        up.animate(element, { transform: "translateY(100%)" }, options),
    );

    up.compiler("[data-player-queue-panel]", (panel) =>
        playerQueuePanelCompiler(up, panel),
    );
}

function playerQueuePanelCompiler(up, panel) {
    const listElement = panel.querySelector("[data-player-queue-items]");
    const emptyElement = panel.querySelector("[data-player-queue-empty]");

    if (!listElement || !emptyElement) {
        return;
    }

    let tracks = [];
    let currentTrackID = null;
    let playing = false;

    const updateRows = () => {
        for (const row of listElement.querySelectorAll("[data-track-id]")) {
            const active =
                currentTrackID !== null &&
                row.dataset.trackId === String(currentTrackID);
            const rowPlaying = active && playing;
            const playIcon = row.querySelector("[data-player-queue-play-icon]");

            row.classList.toggle("is-active", active);
            row.classList.toggle("is-playing", rowPlaying);

            if (active) {
                row.setAttribute("aria-current", "true");
            } else {
                row.removeAttribute("aria-current");
            }

            if (playIcon) {
                playIcon.toggleAttribute("hidden", !active);
                setIcon(playIcon, rowPlaying ? "pause" : "play");
            }

            const button = row.querySelector("[data-player-queue-index]");
            if (button) {
                const trackIndex = Number(button.dataset.playerQueueIndex);
                const track = tracks[trackIndex];
                const title = track?.title ?? "track";
                const duration = formatTime(track?.durationSeconds);

                button.setAttribute("aria-pressed", String(rowPlaying));
                button.setAttribute(
                    "aria-label",
                    `${rowPlaying ? "Pause" : "Play"} ${title}, duration ${duration}`,
                );
            }
        }
    };

    const renderRows = () => {
        const rows = tracks.map((track, index) =>
            createPlayerQueueRow(track, index),
        );

        listElement.replaceChildren(...rows);
        listElement.hidden = tracks.length === 0;
        emptyElement.hidden = tracks.length !== 0;
        updateRows();
    };

    const handleClick = (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }

        const selectedButton = event.target.closest(
            "[data-player-queue-index]",
        );
        if (!selectedButton || !panel.contains(selectedButton)) {
            return;
        }

        const selectedIndex = Number(selectedButton.dataset.playerQueueIndex);
        if (!Number.isInteger(selectedIndex)) {
            return;
        }

        up.emit(PLAYER_SELECT_TRACK, { selectedIndex });
    };

    const unbind = [
        up.on(panel, "click", handleClick),
        up.on(PLAYER_QUEUE_CHANGED, (event) => {
            tracks = Array.isArray(event.tracks) ? event.tracks : [];
            renderRows();
        }),
        up.on(PLAYER_TRACK_CHANGED, (event) => {
            currentTrackID = event.trackId;
            playing = false;
            updateRows();
        }),
        up.on(PLAYER_PLAYBACK_CHANGED, (event) => {
            currentTrackID = event.trackId;
            playing = event.playing;
            updateRows();
        }),
    ];

    up.emit(PLAYER_STATE_REQUEST);

    return unbind;
}

function createPlayerQueueRow(track, index) {
    const row = document.createElement("li");
    row.className = "player-queue-row";
    row.dataset.trackId = String(track.id);

    const formattedDuration = formatTime(track.durationSeconds);

    const button = document.createElement("button");
    button.className = "player-queue-track";
    button.type = "button";
    button.dataset.playerQueueIndex = String(index);
    button.setAttribute(
        "aria-label",
        `Play ${track.title}, duration ${formattedDuration}`,
    );
    button.setAttribute("aria-pressed", "false");

    const playIcon = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "svg",
    );
    playIcon.classList.add("audio-icon", "player-queue-play-icon");
    playIcon.setAttribute("aria-hidden", "true");

    const playIconUse = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "use",
    );
    playIconUse.setAttribute("href", "#icon-play");
    playIconUse.setAttribute("data-player-queue-play-icon", "");
    playIconUse.setAttribute("hidden", "");
    playIcon.append(playIconUse);

    const position = document.createElement("span");
    position.className = "player-queue-position";
    position.textContent = `${index + 1}`;

    const title = document.createElement("span");
    title.className = "player-queue-title";
    title.textContent = track.title;
    title.title = track.title;

    const duration = document.createElement("span");
    duration.className = "player-queue-duration";
    duration.textContent = formattedDuration;
    duration.setAttribute("aria-hidden", "true");

    button.append(playIcon, position, title, duration);
    row.append(button);

    return row;
}
