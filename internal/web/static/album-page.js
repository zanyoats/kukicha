import {
    PLAYER_PLAYBACK_CHANGED,
    PLAYER_SELECT_QUEUE,
    PLAYER_STATE_REQUEST,
    PLAYER_TRACK_CHANGED,
} from "./player-events.js";
import { setIcon } from "./player-utils.js";

export default function registerAlbumPage(up) {
    up.compiler("[data-album-page]", (albumPage, data) =>
        albumPageCompiler(up, albumPage, data),
    );
}

function albumPageCompiler(up, albumPageElement, data) {
    const tracks = Array.isArray(data.tracks) ? data.tracks : [];
    let currentTrackID = null;
    let playing = false;

    const updateRows = () => {
        for (const row of albumPageElement.querySelectorAll(
            ".track-row[data-track-id]",
        )) {
            const active =
                currentTrackID !== null &&
                row.dataset.trackId === String(currentTrackID);
            const rowPlaying = active && playing;

            row.classList.toggle("is-active", active);
            if (active) {
                row.setAttribute("aria-current", "true");
            } else {
                row.removeAttribute("aria-current");
            }

            const button = row.querySelector('[data-player-action="play"]');
            const icon = row.querySelector("[data-track-play-icon]");
            if (button) {
                const trackIndex = Number(button.dataset.trackIndex);
                const title = tracks[trackIndex].title;
                button.setAttribute(
                    "aria-pressed",
                    rowPlaying ? "true" : "false",
                );
                button.setAttribute(
                    "aria-label",
                    `${rowPlaying ? "Pause" : "Play"} ${title}`,
                );
            }
            setIcon(icon, rowPlaying ? "pause" : "play");
        }
    };

    const handleClick = (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }

        const selectedButton = event.target.closest(
            '[data-player-action="play"][data-track-index]',
        );
        if (!selectedButton || !albumPageElement.contains(selectedButton)) {
            return;
        }

        event.preventDefault();

        const selectedIndex = Number(selectedButton.dataset.trackIndex);
        if (
            !Number.isInteger(selectedIndex) ||
            selectedIndex < 0 ||
            selectedIndex >= tracks.length
        ) {
            return;
        }

        up.emit(PLAYER_SELECT_QUEUE, { tracks, selectedIndex });
    };

    const unbind = [
        up.on(albumPageElement, "click", handleClick),
        up.on(PLAYER_TRACK_CHANGED, (event) => {
            currentTrackID = event.trackId;
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
