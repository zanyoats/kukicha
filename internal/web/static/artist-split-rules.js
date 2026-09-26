import { requestJSON } from "./json-api.js";

export default function registerArtistSplitRules(up) {
    up.compiler("[data-split-rule]", (row, data) => {
        let destroyed = false;
        const list = row.querySelector("[data-split-artists]");
        const edit = row.querySelector("[data-split-edit]");
        const form = row.querySelector("[data-split-form]");
        const textarea = form.querySelector("textarea");
        const fieldset = form.querySelector("fieldset");
        const error = form.querySelector("[role=alert]");
        let saved = [...(data.mapped_artists ?? [])];
        let saving = false;

        const close = () => {
            textarea.value = saved.join("\n");
            form.hidden = true;
            list.hidden = false;
            edit.hidden = false;
            edit.setAttribute("aria-expanded", "false");
            edit.focus();
        };
        const showError = (message) => {
            error.textContent = message;
            error.hidden = false;
        };

        const unbindEdit = up.on(edit, "click", () => {
            textarea.value = saved.join("\n");
            error.hidden = true;
            form.hidden = false;
            list.hidden = true;
            edit.hidden = true;
            edit.setAttribute("aria-expanded", "true");
            textarea.focus();
        });
        const unbindCancel = up.on(form.querySelector("[data-split-cancel]"), "click", close);
        const unbindSubmit = up.on(form, "submit", async (event) => {
            event.preventDefault();
            if (saving) return;
            const artists = textarea.value.split(/\r\n|\r|\n/)
                .map((artist) => artist.trim()).filter(Boolean);
            error.hidden = true;
            if (!artists.length) {
                showError("Enter at least one artist.");
                textarea.focus();
                return;
            }
            saving = true;
            fieldset.disabled = true;
            form.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, data.update_url, {
                    method: "PATCH",
                    body: {
                        album_key: data.album_key,
                        mapped_artists: artists,
                    },
                    errorMessage: "Unable to save. Please try again.",
                });
                if (destroyed) return;
                saved = artists;
                list.replaceChildren(...saved.map((artist) => {
                    const item = document.createElement("li");
                    item.className = "text-truncate";
                    item.title = artist;
                    item.textContent = artist;
                    return item;
                }));
                close();
            } catch (failure) {
                if (destroyed) return;
                showError(failure.message);
            } finally {
                saving = false;
                fieldset.disabled = false;
                form.removeAttribute("aria-busy");
            }
        });
        return [
            () => { destroyed = true; },
            unbindEdit,
            unbindCancel,
            unbindSubmit,
        ];
    });
}
