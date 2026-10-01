import { requestJSON } from "./json-api.js";
import { showToast } from "./notifications.js";

export default function registerSyncJob(up) {
    up.compiler("[data-sync-job]", (container) => {
        const button = container.querySelector("[data-sync-submit]");
        let submitting = false;
        let destroyed = false;
        let enableTimer;

        const unbind = up.on(button, "click", async (event) => {
            event.preventDefault();
            if (submitting) return;
            submitting = true;
            const submittedAt = performance.now();
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, button.dataset.syncSubmit, {
                    method: "POST",
                    body: { type: "sync", payload: {} },
                    errorMessage:
                        "Unable to submit the sync job. Please try again.",
                });
                showToast("Sync job queued.", { trigger: button });
            } catch (failure) {
                showToast(failure.message, { role: "alert", trigger: button });
            } finally {
                if (!destroyed) {
                    const enable = () => {
                        submitting = false;
                        button.disabled = false;
                        button.removeAttribute("aria-busy");
                    };
                    // Keep quick responses from flashing the button's disabled state.
                    const remaining = 3000 - (performance.now() - submittedAt);
                    if (remaining > 0)
                        enableTimer = setTimeout(enable, remaining);
                    else enable();
                }
            }
        });
        return [
            () => {
                destroyed = true;
                clearTimeout(enableTimer);
            },
            unbind,
        ];
    });
}
