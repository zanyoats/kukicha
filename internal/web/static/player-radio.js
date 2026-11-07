// Per-tab radio progress. The server resolves positions but owns no cursor.
export function createRadioSession({ url, onSelection, onStatus, onError,
    storage, request = fetch }) {
    const storageKey = `kukicha:radio:v1:${url}`;
    let saved = { active: null, radios: {} };
    let current = null;
    let pending = false;
    let generation = 0;
    let controller;
    try {
        const parsed = JSON.parse(storage?.getItem(storageKey) || "null");
        if (parsed && typeof parsed.radios === "object" && parsed.radios !== null) {
            for (const [key, value] of Object.entries(parsed.radios)) {
                if (validRadio(value) && key === keyFor(value)) saved.radios[key] = value;
            }
            if (typeof parsed.active === "string" && saved.radios[parsed.active]) {
                saved.active = parsed.active;
            }
        }
    } catch { /* Storage may be unavailable or contain an older format. */ }

    function persist() {
        try { storage?.setItem(storageKey, JSON.stringify(saved)); } catch {
            // Playback remains usable in memory when storage is unavailable/full.
        }
    }
    function cancel() {
        generation++;
        controller?.abort();
        pending = false;
    }
    function leave() {
        cancel();
        current = null;
        saved.active = null;
        persist();
        onStatus();
    }
    async function send(body, signal) {
        const response = await request(url, {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body), signal,
        });
        let data;
        try { data = await response.json(); } catch {
            throw new Error("Unable to load this radio. Please try again.");
        }
        if (!response.ok) {
            const error = new Error(data.error || "Unable to load this radio.");
            error.code = data.code;
            error.status = response.status;
            throw error;
        }
        return data;
    }
    async function load(target, action, state, autoplay) {
        cancel();
        const ticket = generation;
        controller = new AbortController();
        const signal = controller.signal;
        pending = true;
        onStatus();
        try {
            let result;
            const body = { kind: target.kind, entity_id: target.entity_id, action };
            if (state) body.state = state;
            try {
                result = await send(body, signal);
            } catch (error) {
                if (ticket !== generation) return;
                if (error.code !== "radio_membership_changed" && !(state && error.status === 400)) throw error;
                // A rebuild invalidates every old permutation. Restart once;
                // never apply old parameters to the newly numbered membership.
                result = await send({ kind: target.kind, entity_id: target.entity_id,
                    action: "start" }, signal);
            }
            if (ticket !== generation) return;
            const next = { kind: target.kind, entity_id: target.entity_id,
                name: result.name, state: result.state };
            if (!validRadio(next) || !result.track?.id || !result.track?.audioURL) {
                throw new Error("Invalid radio response. Please try again.");
            }
            current = next;
            saved.radios[keyFor(next)] = next;
            saved.active = keyFor(next);
            persist();
            pending = false;
            onSelection(result.track, autoplay);
        } catch (error) {
            if (ticket !== generation) return;
            const unavailable = error.status === 404 && !!current &&
                keyFor(current) === keyFor(target);
            if (error.status === 404) {
                if (unavailable) current = null;
                delete saved.radios[keyFor(target)];
                if (saved.active === keyFor(target)) saved.active = null;
                persist();
            }
            onError(error, unavailable);
        } finally {
            if (ticket === generation) {
                pending = false;
                onStatus();
            }
        }
    }
    function start(kind, entityID, autoplay = true) {
        const target = { kind, entity_id: String(entityID) };
        const existing = saved.radios[keyFor(target)];
        return load(target, existing ? "track" : "start", existing?.state, autoplay);
    }
    function move(direction) {
        if (!current || pending) return;
        const position = BigInt(current.state.current_position);
        if (direction === -1 && position === 0n) return;
        const next = position + BigInt(direction);
        if (next === BigInt(current.state.track_count)) {
            return load(current, "cycle", current.state, true);
        }
        return load(current, "track", { ...current.state,
            current_position: String(next) }, true);
    }
    return {
        get current() { return current; },
        get pending() { return pending; },
        start, move, leave,
        restore() {
            const target = saved.radios[saved.active];
            if (target) return start(target.kind, target.entity_id, false);
        },
        dispose: cancel,
    };
}

function keyFor(radio) { return `${radio.kind}:${radio.entity_id}`; }
function validRadio(radio) {
    if (!radio || typeof radio.name !== "string" || !["artist", "genre", "style"].includes(radio.kind) ||
        typeof radio.entity_id !== "string" || !/^[1-9][0-9]{0,18}$/.test(radio.entity_id)) return false;
    const state = radio.state;
    if (!state || typeof state.membership_version !== "string" ||
        !state.membership_version || state.membership_version.length > 128) return false;
    for (const key of ["track_count", "prime", "multiplier", "offset", "current_position"]) {
        if (typeof state[key] !== "string" || !/^(0|[1-9][0-9]{0,18})$/.test(state[key])) return false;
    }
    const count = BigInt(state.track_count), prime = BigInt(state.prime);
    return count > 0n && prime > count && BigInt(state.multiplier) > 0n &&
        BigInt(state.multiplier) < prime && BigInt(state.offset) < prime &&
        BigInt(state.current_position) < count;
}
