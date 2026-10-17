import { requestJSON } from "./json-api.js";
import { showToast } from "./notifications.js";

export default function registerSyncJob(up) {
    up.compiler("[data-library-job-submit]", (button) => {
        const jobType = button.dataset.jobType;
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
                await requestJSON(up, button.dataset.libraryJobSubmit, {
                    method: "POST",
                    body: { type: jobType, payload: {} },
                    errorMessage: `Unable to submit the ${jobType} job. Please try again.`,
                });
                showToast(`${jobType} job queued.`, { trigger: button });
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
