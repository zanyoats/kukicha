import { showToast } from "./notifications.js";
import { requestJSON } from "./json-api.js";

export default function registerArtistRules(up) {
    up.compiler("[data-split-rule]", (row, data) => {
        let destroyed = false;
        const list = row.querySelector("[data-split-artists]");
        const mapped = row.querySelector("[data-rule-mapped]");
        const arrow = row.querySelector("[data-rule-arrow]");
        const edit = row.querySelector("[data-split-edit]");
        const form = row.querySelector("[data-split-form]");
        const remove = form.querySelector("[data-split-delete]");
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
                edit.textContent = "Edit";
                remove.hidden = false;
                mapped.hidden = false;
                arrow.hidden = false;
                mapped.replaceChildren(...saved.map((artist) => {
                    const item = document.createElement("li");
                    item.className = "artist-rule-example-name artist-rule-example-result";
                    item.title = artist;
                    item.textContent = artist;
                    return item;
                }));
                close();
            } catch (failure) {
                if (destroyed) return;
                showToast(failure.message, { role: "alert" });
            } finally {
                saving = false;
                fieldset.disabled = false;
                form.removeAttribute("aria-busy");
            }
        });
        const unbindDelete = up.on(remove, "click", async () => {
            if (saving) return;
            saving = true;
            error.hidden = true;
            fieldset.disabled = true;
            form.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, data.update_url, {
                    method: "DELETE",
                    body: { album_key: data.album_key },
                    errorMessage: "Unable to delete. Please try again.",
                });
                if (destroyed) return;
                saved = [...(data.original_artists ?? [])];
                edit.textContent = "Add";
                remove.hidden = true;
                mapped.replaceChildren();
                mapped.hidden = true;
                arrow.hidden = true;
                close();
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
        return [
            () => { destroyed = true; },
            unbindEdit,
            unbindCancel,
            unbindSubmit,
            unbindDelete,
        ];
    });
}
