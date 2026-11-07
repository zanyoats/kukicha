import { showToast } from "./notifications.js";
import { requestJSON } from "./json-api.js";

export default function registerUnrecognizedTracks(up) {
    up.compiler("[data-unrecognized-track]", (form, data) => {
        const fieldset = form.querySelector("fieldset");
        const album = form.elements.namedItem("album");
        const artists = form.elements.namedItem("artists");
        const status = form.querySelector("[data-track-status]");
        const error = form.querySelector("[role=alert]");
        let destroyed = false;
        let saving = false;
        const unbindInput = up.on(form, "input", () => {
            status.textContent = "Unsaved changes";
            error.hidden = true;
        });
        const unbindSubmit = up.on(form, "submit", async (event) => {
            event.preventDefault();
            if (saving) return;
            const albumValue = album.value.trim();
            const artistValues = artists.value.split(/\r\n|\r|\n/)
                .map((artist) => artist.trim()).filter(Boolean);
            error.hidden = true;
            if (!albumValue || !artistValues.length) {
                error.textContent = "Enter an album and at least one artist.";
                error.hidden = false;
                (!albumValue ? album : artists).focus();
                return;
            }
            saving = true;
            fieldset.disabled = true;
            form.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, data.update_url, {
                    method: "PATCH",
                    body: {
                        uploaded_path: data.uploaded_path,
                        album: albumValue,
                        artists: artistValues,
                    },
                    errorMessage: "Unable to save this track. Please try again.",
                });
                if (destroyed) return;
                album.value = albumValue;
                artists.value = artistValues.join("\n");
                status.textContent = "Ready for rebuild";
            } catch (failure) {
                if (destroyed) return;
                showToast(failure.message, { role: "alert" });
            } finally {
                if (!destroyed) {
                    saving = false;
                    fieldset.disabled = false;
                    form.removeAttribute("aria-busy");
                }
            }
        });
        return [() => { destroyed = true; }, unbindInput, unbindSubmit];
    });
}
