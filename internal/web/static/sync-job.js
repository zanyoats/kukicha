import { requestJSON } from "./json-api.js";

export default function registerSyncJob(up) {
    up.compiler("[data-sync-job]", (container) => {
        const link = container.querySelector("[data-sync-submit]");
        const message = container.querySelector("[data-job-message]");
        const label = link.textContent;
        let submitting = false;
        let destroyed = false;

        const unbind = up.on(link, "click", async (event) => {
            event.preventDefault();
            if (submitting) return;
            submitting = true;
            message.hidden = true;
            message.textContent = "";
            message.className = "";
            message.setAttribute("role", "status");
            link.textContent = "Submitting…";
            link.setAttribute("aria-disabled", "true");
            link.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, link.href, {
                    method: "POST",
                    body: { type: "sync", payload: {} },
                    errorMessage: "Unable to submit the sync job. Please try again.",
                });
                if (destroyed) return;
                message.hidden = false;
                message.textContent = "Sync job queued.";
            } catch (failure) {
                if (destroyed) return;
                message.className = "alert alert-error";
                message.setAttribute("role", "alert");
                message.hidden = false;
                message.textContent = failure.message;
            } finally {
                submitting = false;
                if (!destroyed) {
                    link.textContent = label;
                    link.removeAttribute("aria-disabled");
                    link.removeAttribute("aria-busy");
                }
            }
        });
        return [
            () => { destroyed = true; },
            unbind,
        ];
    });
}
