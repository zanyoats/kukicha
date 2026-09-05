export function formatTime(value) {
    // Keep this output in sync with formatDuration in internal/web/funcs.go.
    if (!Number.isFinite(value) || value < 0) {
        return "0:00";
    }

    const totalSeconds = Math.floor(value);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = String(totalSeconds % 60).padStart(2, "0");

    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`;
    }

    return `${minutes}:${seconds}`;
}

export function setIcon(icon, name) {
    icon?.setAttribute("href", `#icon-${name}`);
}

export function setRangeProgress(control, value, maximum) {
    const progress =
        Number.isFinite(value) && Number.isFinite(maximum) && maximum > 0
            ? Math.min(100, Math.max(0, (value / maximum) * 100))
            : 0;

    control.style.setProperty("--range-progress", `${progress}%`);
}
