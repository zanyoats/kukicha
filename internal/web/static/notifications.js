const activeAlerts = new WeakMap();

// Shared by page actions and server-driven job status; survives main-content swaps.
export function showToast(message, { role = "status", trigger } = {}) {
    trigger = trigger ? new WeakRef(trigger) : undefined;
    // Keep action errors visible and dismissible inside the active modal as well.
    const front = window.up.layer.front;
    const host = front.isRoot() ? document.body : front.element;
    let alerts;
    if (role === "alert") {
        alerts = activeAlerts.get(host);
        if (!alerts) activeAlerts.set(host, alerts = new Map());
        const existing = alerts.get(message);
        if (existing?.toast.isConnected) return existing.dismiss;
        existing?.remove();
    }
    let layer = host.querySelector(":scope > [data-notifications]");
    if (!layer) {
        const template = document.querySelector("#notification-layer-template");
        if (!template) return;
        layer = template.content.firstElementChild.cloneNode(true);
        host.append(layer);
    }
    const toast = layer
        .querySelector("[data-toast-template]")
        .content.firstElementChild.cloneNode(true);
    const text = toast.querySelector("[data-toast-message]");
    text.setAttribute("role", role);
    const dismiss = toast.querySelector("[data-toast-dismiss]");
    const configuredMinimum = Number(layer.dataset.toastMinDurationMs);
    const minimum =
        Number.isFinite(configuredMinimum) && configuredMinimum > 0
            ? configuredMinimum
            : 3000;
    let remaining = Math.max(minimum, message.length * 60);
    let timer;
    let frame;
    let startedAt;
    let hovered = false;
    let focused = false;
    let removed = false;
    const remove = () => {
        if (removed) return;
        removed = true;
        cancelAnimationFrame(frame);
        alerts?.delete(message);
        clearTimeout(timer);
        document.removeEventListener("visibilitychange", updateTimer);
        toast.remove();
    };
    const updateTimer = () => {
        // Errors stay visible until dismissed by the user or their caller.
        if (role === "alert") return;
        if (timer !== undefined) {
            clearTimeout(timer);
            remaining -= performance.now() - startedAt;
            timer = undefined;
        }
        // `document.hidden` is true when you switch to another tab
        // or minimize the window
        if (removed || hovered || focused || document.hidden) return;
        startedAt = performance.now();
        timer = setTimeout(remove, Math.max(0, remaining));
    };
    toast.addEventListener("mouseenter", () => {
        hovered = true;
        updateTimer();
    });
    toast.addEventListener("mouseleave", () => {
        hovered = false;
        updateTimer();
    });
    toast.addEventListener("focusin", () => {
        focused = true;
        updateTimer();
    });
    toast.addEventListener("focusout", (event) => {
        focused = toast.contains(event.relatedTarget);
        updateTimer();
    });
    if (role !== "alert") document.addEventListener("visibilitychange", updateTimer);
    const dismissToast = () => {
        if (removed) return;
        if (toast.contains(document.activeElement)) {
            const next =
                toast.nextElementSibling?.querySelector("button, a") ??
                toast.previousElementSibling?.querySelector("button, a");
            if (next) next.focus();
            else {
                const target = trigger?.deref();
                if (target?.isConnected) target.focus();
            }
        }
        remove();
    };
    dismiss.addEventListener("click", dismissToast);
    layer.append(toast);
    alerts?.set(message, { toast, dismiss: dismissToast, remove });
    window.up.destructor(toast, remove);
    // Populate the live region after attaching it so assistive technology announces it.
    frame = requestAnimationFrame(() => {
        if (removed) return;
        text.textContent = message;
        updateTimer();
    });
    return dismissToast;
}
