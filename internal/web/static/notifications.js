// Shared by page actions and server-driven job status; survives main-content swaps.
export function showToast(message, { role = "status", trigger } = {}) {
    const layer = document.querySelector("[data-notifications]");
    if (!layer) return;
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
    let startedAt;
    let hovered = false;
    let focused = false;
    let removed = false;
    const remove = () => {
        removed = true;
        clearTimeout(timer);
        document.removeEventListener("visibilitychange", updateTimer);
        toast.remove();
    };
    const updateTimer = () => {
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
    document.addEventListener("visibilitychange", updateTimer);
    dismiss.addEventListener("click", () => {
        if (toast.contains(document.activeElement)) {
            const next =
                toast.nextElementSibling?.querySelector("button, a") ??
                toast.previousElementSibling?.querySelector("button, a");
            if (next) next.focus();
            else if (trigger?.isConnected) trigger.focus();
        }
        remove();
    });
    layer.append(toast);
    // Populate the live region after attaching it so assistive technology announces it.
    requestAnimationFrame(() => {
        text.textContent = message;
        updateTimer();
    });
}
