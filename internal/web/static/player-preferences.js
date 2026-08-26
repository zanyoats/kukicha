const volumeStorageKey = "kukicha.player.volume";

export function loadPreferredVolume() {
    try {
        const storedValue = localStorage.getItem(volumeStorageKey);
        if (storedValue === null) {
            return 1;
        }

        const value = Number(storedValue);
        if (Number.isFinite(value) && value >= 0 && value <= 1) {
            return value;
        }
    } catch {
        // Use the default when storage is unavailable.
    }

    return 1;
}

export function savePreferredVolume(value) {
    try {
        localStorage.setItem(volumeStorageKey, String(value));
    } catch {
        // The selected volume remains active for this page lifecycle.
    }
}
