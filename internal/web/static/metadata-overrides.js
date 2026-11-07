import { showToast } from "./notifications.js";
import { requestJSON } from "./json-api.js";

export default function registerMetadataOverrides(up) {
    up.compiler("[data-metadata-override]", (form, data) => {
        const input = form.querySelector("input");
        const fieldset = form.querySelector("fieldset");
        const remove = form.querySelector("[data-override-remove]");
        const error = form.querySelector("[role=alert]");
        const status = form.querySelector("[role=status]");
        let saved = input.value;
        let saving = false;
        let destroyed = false;

        const update = async (method) => {
            if (saving) return;
            const value = input.value.trim();
            saving = true;
            fieldset.disabled = true;
            form.setAttribute("aria-busy", "true");
            error.hidden = true;
            status.textContent = "";
            try {
                await requestJSON(up, data.update_url, {
                    method,
                    body: { album_key: data.album_key, ...(method === "PUT" ? { url: value } : {}) },
                    errorMessage: "Unable to update the override. Please try again.",
                });
                if (destroyed) return;
                saved = method === "PUT" ? value : "";
                input.value = saved;
                status.textContent = method === "PUT"
                    ? "Saved. Rebuild to apply."
                    : "Removed. Rebuild to apply.";
            } catch (failure) {
                if (destroyed) return;
                showToast(failure.message, { role: "alert" });
            } finally {
                if (!destroyed) {
                    saving = false;
                    fieldset.disabled = false;
                    remove.disabled = !saved;
                    form.removeAttribute("aria-busy");
                    if (method === "DELETE" && !saved) input.focus();
                }
            }
        };

        const unbindSubmit = up.on(form, "submit", (event) => {
            event.preventDefault();
            update("PUT");
        });
        const unbindRemove = up.on(remove, "click", () => update("DELETE"));
        return [() => { destroyed = true; }, unbindSubmit, unbindRemove];
    });
}
