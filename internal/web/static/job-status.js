import { requestJSON } from "./json-api.js";
import { showToast } from "./notifications.js";

export default function registerJobStatus(up) {
    up.compiler("[data-job-events]", (container) => {
        const snackbar = container.querySelector("[data-job-snackbar]");
        const description = container.querySelector("[data-job-description]");
        const cancel = container.querySelector("[data-job-cancel]");
        const labels = new Map(
            [
                ...container.querySelector("[data-job-labels]").content
                    .children,
            ].map((label) => [label.dataset.type, label.textContent]),
        );
        let job;
        let connected = false;
        let destroyed = false;
        const cancelling = new Set();
        const render = () => {
            // move keyboard focus when a job disappears if it was in the snackbar
            if (!job && snackbar.contains(document.activeElement)) {
                document.querySelector(".top-nav nav a")?.focus();
            }
            snackbar.hidden = !job;
            if (!job) return;
            const label = labels.get(job.type) ?? job.type;
            const pending = job.cancel_requested || cancelling.has(job.id);
            const status = !connected
                ? "Reconnecting…"
                : pending
                  ? "Cancelling…"
                  : "Running";
            const message = `${label} · ${status}`;
            if (description.textContent !== message)
                description.textContent = message;
            cancel.setAttribute("aria-disabled", String(!connected || pending));
            cancel.setAttribute("aria-label", `Cancel ${label}`);
        };
        const source = new EventSource(container.dataset.jobEvents);
        source.addEventListener("job-status", (event) => {
            const jobs = JSON.parse(event.data).jobs ?? [];
            job = jobs.find((candidate) => candidate.state === "running");
            connected = true;
            for (const id of cancelling) {
                if (!jobs.some((candidate) => candidate.id === id))
                    cancelling.delete(id);
            }
            render();
        });
        source.addEventListener("job-notification", (event) => {
            const notification = JSON.parse(event.data);
            if (notification.failed) {
                showToast(notification.message, { role: "alert" });
                return;
            }
            showToast(notification.message, {
                role: "status",
            });
        });
        source.addEventListener("error", () => {
            connected = false;
            render();
        });
        const unbind = up.on(cancel, "click", async (event) => {
            event.preventDefault();
            if (!job || cancel.getAttribute("aria-disabled") === "true") return;
            const id = job.id;
            cancelling.add(id);
            render();
            try {
                await requestJSON(up, container.dataset.jobCancelUrl, {
                    method: "POST",
                    body: { job_id: id },
                    errorMessage: "Unable to cancel the job. Please try again.",
                });
            } catch (failure) {
                cancelling.delete(id);
                if (!destroyed) {
                    showToast(failure.message, {
                        role: "alert",
                        trigger: cancel,
                    });
                    render();
                }
            }
        });
        return [
            unbind,
            () => {
                destroyed = true;
                source.close();
            },
        ];
    });
}
