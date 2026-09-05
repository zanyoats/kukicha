import {
    PLAYER_NEXT,
    PLAYER_PLAYBACK_CHANGED,
    PLAYER_PREVIOUS,
    PLAYER_SEEK,
    PLAYER_SET_VOLUME,
    PLAYER_STATE_REQUEST,
    PLAYER_TIME_CHANGED,
    PLAYER_TOGGLE,
    PLAYER_TOGGLE_MUTE,
    PLAYER_TRACK_CHANGED,
    PLAYER_VOLUME_CHANGED,
} from "./player-events.js";
import {
    formatTime,
    setIcon,
    setRangeProgress,
} from "./player-utils.js";

export default function registerPlayerControls(up) {
    up.compiler("#player", (player) => playerControlsCompiler(up, player));
}

function playerControlsCompiler(up, player) {
    const albumElement = player.querySelector("[data-player-album]");
    const albumLinks = player.querySelectorAll("[data-player-album-link]");
    const artistsElement = player.querySelector(
        "[data-player-artists]",
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
    const queueButton = player.querySelector('[data-player-action="queue"]');
    const muteButton = player.querySelector('[data-player-action="mute"]');
    const playIcon = player.querySelector("[data-player-play-icon]");
    const volumeIcon = player.querySelector("[data-player-volume-icon]");

    if (
        !albumElement ||
        !artistsElement ||
        !artworkElement ||
        !titleElement ||
        !currentTimeElement ||
        !durationElement ||
        !seekControl ||
        !volumeControl ||
        !playButton ||
        !previousButton ||
        !nextButton ||
        !queueButton ||
        !muteButton ||
        !playIcon ||
        !volumeIcon
    ) {
        return;
    }

    const updateNowPlaying = (event) => {
        const track = event.track;
        for (const link of albumLinks) {
            if (track?.albumURL) {
                link.setAttribute("href", track.albumURL);
            } else {
                link.removeAttribute("href");
            }
        }
        if (!track) {
            titleElement.textContent = "Choose a track";
            titleElement.removeAttribute("title");
            albumElement.textContent = "";
            albumElement.removeAttribute("title");
            artistsElement.textContent = "";
            artistsElement.removeAttribute("title");
            artistsElement.hidden = true;
            artworkElement.removeAttribute("src");
            artworkElement.removeAttribute("width");
            artworkElement.removeAttribute("height");
            artworkElement.alt = "";
            artworkElement.hidden = true;
            return;
        }

        titleElement.textContent = track.title;
        titleElement.title = track.title;
        albumElement.textContent = track.album;
        albumElement.title = track.album;
        artistsElement.textContent = track.artists;
        artistsElement.title = track.artists;
        artistsElement.hidden = track.artists === "";

        if (track.artworkURL) {
            if (track.artworkWidth > 0 && track.artworkHeight > 0) {
                artworkElement.width = track.artworkWidth;
                artworkElement.height = track.artworkHeight;
            } else {
                artworkElement.removeAttribute("width");
                artworkElement.removeAttribute("height");
            }
            artworkElement.src = track.artworkURL;
            artworkElement.alt = `Artwork for ${track.album}`;
            artworkElement.hidden = false;
        } else {
            artworkElement.removeAttribute("src");
            artworkElement.removeAttribute("width");
            artworkElement.removeAttribute("height");
            artworkElement.alt = "";
            artworkElement.hidden = true;
        }
    };

    const updateTransport = (event) => {
        playButton.disabled = !event.hasTrack;
        playButton.setAttribute(
            "aria-label",
            event.playing ? "Pause" : "Play",
        );
        setIcon(playIcon, event.playing ? "pause" : "play");
        previousButton.disabled = !event.hasTrack;
        nextButton.disabled = !event.hasNext;
    };

    const updateTimeline = (event) => {
        const loadingTrack = event.hasTrack && event.duration <= 0;

        seekControl.max = String(event.duration);
        seekControl.value = String(
            Math.min(event.currentTime, event.duration),
        );
        seekControl.disabled = event.duration <= 0;
        seekControl.classList.toggle("is-loading", loadingTrack);
        currentTimeElement.textContent = formatTime(event.currentTime);
        durationElement.textContent = formatTime(event.duration);
        setRangeProgress(seekControl, event.currentTime, event.duration);
    };

    const updateVolume = (event) => {
        const effectiveVolume = event.muted ? 0 : event.volume;

        volumeControl.value = String(effectiveVolume);
        setRangeProgress(volumeControl, effectiveVolume, 1);
        muteButton.setAttribute(
            "aria-label",
            effectiveVolume === 0 ? "Unmute" : "Mute",
        );
        setIcon(volumeIcon, effectiveVolume === 0 ? "volume-off" : "volume");
    };

    const isQueueLayer = (layer) =>
        layer?.element?.matches("up-drawer.player-queue-drawer");

    const updateQueueButton = (expanded) => {
        queueButton.setAttribute("aria-expanded", String(expanded));
        queueButton.setAttribute(
            "aria-label",
            expanded ? "Close queue" : "Open queue",
        );
    };

    const handleClick = (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }

        const queueLayer = up.layer.front;
        if (isQueueLayer(queueLayer)) {
            const queueControl = event.target.closest(
                '[data-player-action="queue"]',
            );
            if (queueControl && player.contains(queueControl)) {
                event.preventDefault();
            }
            queueLayer.dismiss(null, { origin: event.target });
        }

        const control = event.target.closest("[data-player-action]");
        if (!control || !player.contains(control)) {
            return;
        }

        switch (control.dataset.playerAction) {
        case "toggle":
            up.emit(PLAYER_TOGGLE);
            break;
        case "previous":
            up.emit(PLAYER_PREVIOUS);
            break;
        case "next":
            up.emit(PLAYER_NEXT);
            break;
        case "queue":
            break;
        case "mute":
            up.emit(PLAYER_TOGGLE_MUTE);
            break;
        }
    };

    const unbind = [
        up.on(player, "click", handleClick),
        up.on(seekControl, "input", () => {
            up.emit(PLAYER_SEEK, { value: Number(seekControl.value) });
        }),
        up.on(volumeControl, "input", () => {
            up.emit(PLAYER_SET_VOLUME, {
                value: Number(volumeControl.value),
            });
        }),
        up.on(PLAYER_TRACK_CHANGED, (event) => {
            updateNowPlaying(event);
        }),
        up.on(PLAYER_PLAYBACK_CHANGED, updateTransport),
        up.on(PLAYER_TIME_CHANGED, updateTimeline),
        up.on(PLAYER_VOLUME_CHANGED, updateVolume),
        up.on("up:layer:opened", (event) => {
            if (isQueueLayer(event.layer)) {
                updateQueueButton(true);
            }
        }),
        up.on("up:layer:dismissed", (event) => {
            if (isQueueLayer(event.layer)) {
                updateQueueButton(false);
            }
        }),
    ];

    const footerSizeObserver = new ResizeObserver(() => {
        document.documentElement.style.setProperty(
            "--player-footer-block-size",
            `${player.getBoundingClientRect().height}px`,
        );
    });
    footerSizeObserver.observe(player);
    unbind.push(() => {
        footerSizeObserver.disconnect();
        document.documentElement.style.removeProperty("--player-footer-block-size");
    });

    up.emit(PLAYER_STATE_REQUEST);

    return unbind;
}
