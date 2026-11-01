import { requestJSON } from "./json-api.js";
import { showToast } from "./notifications.js";

export default function registerRoots(up) {
    up.compiler("[data-lost-root]", (row) => {
        const button = row.querySelector("[data-drop-root]");
        let submitting = false;
        let destroyed = false;
        const unbind = up.on(button, "click", async (event) => {
            event.preventDefault();
            if (submitting) return;
            submitting = true;
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, button.dataset.dropRoot, {
                    method: "POST",
                    body: { config_id: button.dataset.rootId },
                    errorMessage: "Unable to drop the root. Please try again.",
                });
                if (destroyed) return;
                const section = row.closest(".lost-roots");
                const heading = section.closest("article").querySelector("h1");
                unbind();
                row.remove();
                const next = section.querySelector("[data-drop-root]");
                if (!next) section.hidden = true;
                const focusTarget = next || heading;
                if (focusTarget) {
                    if (!next) focusTarget.setAttribute("tabindex", "-1");
                    focusTarget.focus();
                }
                showToast(`Root “${button.dataset.rootId}” marked for removal. Its saved library data will be removed during the next build.`, { trigger: focusTarget });
            } catch (failure) {
                if (!destroyed) showToast(failure.message, { role: "alert", trigger: button });
            } finally {
                submitting = false;
                if (!destroyed) {
                    button.disabled = false;
                    button.removeAttribute("aria-busy");
                }
            }
        });
        return [() => { destroyed = true; }, unbind];
    });
}
