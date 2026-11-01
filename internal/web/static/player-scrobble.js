// A play keeps its start timestamp across pause/resume, buffering and retries.
export function createScrobbler({ url, onError, request = fetch }) {
    let current = null;
    let lastTime = 0;
    let disposed = false;
    const pending = new Set();

    async function send(entry) {
        if (disposed) return;
        entry.controller = new AbortController();
        const timeout = setTimeout(() => entry.controller.abort(), 10000);
        try {
            const response = await request(url, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: entry.body,
                signal: entry.controller.signal,
            });
            if (response.ok) {
                pending.delete(entry);
                return;
            }
            if (response.status !== 408 && response.status !== 429 && response.status < 500) {
                pending.delete(entry);
                if (!disposed) onError();
                return;
            }
        } catch { /* Retry network failures and timeouts with the identical body. */ }
        finally { clearTimeout(timeout); }
        if (disposed) return;
        if (++entry.attempt >= 4) {
            pending.delete(entry);
            onError();
            return;
        }
        entry.timer = setTimeout(() => send(entry), 1000 * 2 ** (entry.attempt - 1));
    }

    function report(play, submission) {
        const entry = { body: JSON.stringify({ ...play, submission }), attempt: 0 };
        pending.add(entry);
        void send(entry);
    }

    return {
        start(id) {
            if (disposed || !id || current) return;
            // Give each new play a later timestamp, even if the clock hasn't advanced or moves backward.
            lastTime = Math.max(Date.now(), lastTime + 1);
            current = { id: String(id), time: lastTime };
            report(current, false);
        },
        finish() {
            if (!current) return;
            report(current, true);
            current = null;
        },
        select() { current = null; },
        dispose() {
            disposed = true;
            for (const entry of pending) {
                clearTimeout(entry.timer);
                entry.controller?.abort();
            }
            pending.clear();
        },
    };
}
