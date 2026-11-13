import { PLAYER_APPEND_QUEUE, PLAYER_SELECT_QUEUE, PLAYER_SELECT_TRACK } from "./player-events.js";
import { groupByDirectory } from "./path-groups.js";
import { showToast } from "./notifications.js";

// A cancelled queue addition must stop waiting without cancelling earlier additions.
function waitForAppend(previous, signal) {
    return new Promise((resolve, reject) => {
        const abort = () => reject(signal.reason);
        if (signal.aborted) return abort();
        signal.addEventListener("abort", abort, { once: true });
        previous.then(() => {
            signal.removeEventListener("abort", abort);
            resolve();
        }, (error) => {
            signal.removeEventListener("abort", abort);
            reject(error);
        });
    });
}

export default function registerAlbumCover(up) {
    let playRevision = 0;
    let appendTail = Promise.resolve();
    // Direct track selections also supersede any pending album Play request.
    up.on(PLAYER_SELECT_QUEUE, () => { playRevision++; });
    up.on(PLAYER_SELECT_TRACK, () => { playRevision++; });

    up.compiler("[data-album-cover]", (cover) => {
        let tracks;
        let disposed = false;
        let controller;
        const unbind = up.on(cover, "click", async (event) => {
            const button = event.target.closest("[data-album-action]");
            if (!button || button.disabled) return;
            event.preventDefault();
            controller = new AbortController();
            const { signal } = controller;
            const append = button.dataset.albumAction === "append";
            const revision = append ? playRevision : ++playRevision;
            const previousAppend = appendTail;
            let finishAppend;
            if (append) {
                const finished = new Promise((resolve) => { finishAppend = resolve; });
                // Fetch concurrently, but reserve the commit order at click time.
                appendTail = previousAppend.then(() => finished);
            }
            const buttons = cover.querySelectorAll("button");
            buttons.forEach((item) => { item.disabled = true; });
            try {
                if (!tracks) {
                    const page = cover.closest("[data-album-page]");
                    if (page) {
                        tracks = JSON.parse(page.getAttribute("up-data")).tracks;
                    } else {
                        const timeout = setTimeout(() => controller.abort(), 30000);
                        try {
                            const response = await fetch(cover.dataset.albumTracksUrl, { signal });
                            if (!response.ok) throw new Error("Unable to load album");
                            const result = await response.json();
                            signal.throwIfAborted();
                            tracks = result.tracks;
                        } finally {
                            clearTimeout(timeout);
                        }
                    }
                }
                if (append) await waitForAppend(previousAppend, signal);
                signal.throwIfAborted();
                if (disposed || (!append && revision !== playRevision)) return;
                if (!Array.isArray(tracks) || !tracks.length) {
                    showToast("This album has no tracks.", { trigger: button });
                    return;
                }
                tracks = [...groupByDirectory(tracks, (track) => track.path).values()].flat();
                up.emit(append ? PLAYER_APPEND_QUEUE : PLAYER_SELECT_QUEUE, {
                    tracks, selectedIndex: 0, startAlbum: true,
                });
                if (append) showToast("Album added to queue.", { trigger: button });
            } catch {
                if (!disposed && (append || revision === playRevision)) showToast("Unable to load album tracks. Please try again.", {
                    role: "alert", trigger: button,
                });
            } finally {
                finishAppend?.();
                if (!disposed) buttons.forEach((item) => { item.disabled = false; });
                controller = undefined;
            }
        });
        return () => {
            disposed = true;
            controller?.abort();
            tracks = undefined;
            unbind();
        };
    });
}
