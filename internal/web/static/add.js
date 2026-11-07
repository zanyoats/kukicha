function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4);
    return `${(bytes / 1024 ** unit).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}

// Directory readers return batches; keep reading until an empty batch arrives.
async function readDroppedEntry(entry, parent, files, signal) {
    signal.throwIfAborted();
    const path = parent ? `${parent}/${entry.name}` : entry.name;
    if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        files.push({ file, path });
    } else if (entry.isDirectory) {
        const reader = entry.createReader();
        while (true) {
            signal.throwIfAborted();
            const children = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
            if (!children.length) break;
            for (const child of children) await readDroppedEntry(child, path, files, signal);
        }
    }
}

export default function registerAdd(up) {
    up.compiler("[data-add-form]", (form) => {
        const maxRequestBytes = BigInt(form.dataset.maxRequestBytes);
        const sizeError = form.querySelector("[data-add-size-error]");
        const limitLabel = `${formatSize(Number(maxRequestBytes))} (${maxRequestBytes} bytes)`;
        const sizeErrorMessage = `Selected files leave no room within the ${limitLabel} upload request limit for multipart headers. Remove files or upload them in smaller batches.`;
        const filesInput = form.querySelector("[data-add-files]");
        const folderInput = form.querySelector("[data-add-folder]");
        const zone = form.querySelector("[data-add-dropzone]");
        const list = form.querySelector("[data-add-list]");
        const status = form.querySelector("[data-add-status]");
        const notice = form.querySelector("[data-add-notice]");
        const clear = form.querySelector("[data-add-clear]");
        const upload = form.querySelector("[data-add-upload]");
        const root = form.querySelector("[name=root_config_id]");
        const pick = form.querySelector("[data-add-pick-files]");
        const mode = form.querySelector("[name=mode]");
        const sourceType = form.querySelector("[name=source_type]");
        const field = (name) => form.elements.namedItem(name).value;
        let uploading = false;
        let reading = false;
        const selected = new Map();
        const controller = new AbortController();
        let dragDepth = 0;
        const on = (element, event, callback) => element.addEventListener(event, callback, { signal: controller.signal });
        on(document, "up:layer:dismiss", (event) => {
            if (uploading && event.layer.element.contains(form)) {
                event.preventDefault();
                notice.textContent = "Keep this dialog open until the build job is queued.";
                notice.hidden = false;
            }
        });
        const selectedBytes = () => {
            let bytes = 0n;
            for (const { file } of selected.values()) bytes += BigInt(file.size);
            return bytes;
        };
        const render = () => {
            const busy = uploading || reading;
            const isFiles = mode.value === "files";
            for (const panel of form.querySelectorAll("[data-add-panel]")) {
                panel.hidden = panel.dataset.addPanel !== mode.value;
                for (const input of panel.querySelectorAll("input, select, button")) {
                    input.disabled = busy || panel.hidden;
                }
            }
            for (const panel of form.querySelectorAll("[data-add-source]")) {
                panel.hidden = panel.dataset.addSource !== sourceType.value;
                for (const input of panel.querySelectorAll("input, select, button")) {
                    input.disabled = busy || isFiles || panel.hidden;
                }
            }
            mode.disabled = busy;
            clear.hidden = !isFiles;
            const fragment = document.createDocumentFragment();
            let bytes = 0;
            for (const [key, { file, path }] of selected) {
                bytes += file.size;
                const row = document.createElement("li");
                const name = document.createElement("span");
                name.textContent = path;
                const size = document.createElement("span");
                size.className = "text-secondary";
                size.textContent = formatSize(file.size);
                const remove = document.createElement("button");
                remove.type = "button";
                remove.disabled = busy;
                remove.className = "action-button";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", `Remove ${path}`);
                remove.dataset.removeKey = key;
                row.append(name, size, remove);
                fragment.append(row);
            }
            list.replaceChildren(fragment);
            list.hidden = selected.size === 0;
            clear.disabled = busy || selected.size === 0;
            const oversized = isFiles && selected.size > 0 && selectedBytes() >= maxRequestBytes;
            sizeError.hidden = !oversized;
            sizeError.textContent = oversized ? sizeErrorMessage : "";
            upload.disabled = busy || (isFiles && selected.size === 0) || oversized || !root.value;
            upload.textContent = isFiles ? (uploading ? "Uploading…" : "Upload") : (uploading ? "Queueing…" : "Add music");
            root.disabled = busy || root.options.length <= 1;
            status.textContent = reading ? "Reading folders…" : selected.size
                ? `${selected.size} ${selected.size === 1 ? "file" : "files"} selected · ${formatSize(bytes)}`
                : "No files selected.";
        };
        const collect = (files) => {
            if (uploading || mode.value !== "files") return;
            for (const { file, path } of files) {
                const key = JSON.stringify([path, file.size, file.lastModified]);
                selected.set(key, { file, path });
            }
            notice.hidden = true;
            render();
        };
        on(root, "change", render);
        for (const selector of [mode, sourceType]) {
            on(selector, "change", () => {
                notice.hidden = true;
                render();
            });
        }
        on(form, "submit", async (event) => {
            event.preventDefault();
            const isFiles = mode.value === "files";
            if (uploading || reading || (isFiles && !selected.size) || !root.value || !form.reportValidity()) return;
            if (isFiles && selectedBytes() >= maxRequestBytes) {
                render();
                return;
            }
            let body;
            const headers = {};
            if (isFiles) {
                body = new FormData();
                for (const { file, path } of selected.values()) body.append("files", file, path);
            } else {
                const request = { mode: "sync" };
                if (sourceType.value === "folder") {
                    request.folder = field("folder");
                } else {
                    request.remote = { credentials: {
                        access_key_id: field("access_key_id").trim(),
                        secret_access_key: field("secret_access_key").trim(),
                        session_token: field("session_token").trim(),
                    } };
                    for (const name of ["endpoint_url", "bucket", "prefix", "region", "addressing_style"]) {
                        const value = field(name);
                        if (value) request.remote[name] = value;
                    }
                }
                body = JSON.stringify(request);
                headers["Content-Type"] = "application/json";
            }
            const url = new URL(form.action, window.location.href);
            url.searchParams.set("root_id", root.value);
            uploading = true;
            form.setAttribute("aria-busy", "true");
            notice.hidden = true;
            render();
            try {
                const response = await fetch(url, {
                    method: "POST",
                    body,
                    headers,
                    signal: controller.signal,
                });
                const login = response.headers.get("X-Kukicha-Login");
                if (login) {
                    window.location.assign(login);
                    return;
                }
                // Proxies may return a non-JSON 413 response.
                if (response.status === 413) {
                    let message = `Upload is too large. The configured request limit is ${limitLabel}, including multipart headers; a proxy may impose a lower limit. Try a smaller batch.`;
                    try {
                        const result = await response.json();
                        if (typeof result.error === "string" && result.error.trim()) message = result.error;
                    } catch {
                        // Keep the size-specific fallback if no JSON body is available.
                    }
                    throw new Error(message);
                }
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "Unable to add music. Please try again.");
                if (isFiles) selected.clear();
                else if (sourceType.value === "remote") {
                    for (const name of ["access_key_id", "secret_access_key", "session_token"]) {
                        form.elements.namedItem(name).value = "";
                    }
                }
                notice.textContent = `${isFiles ? "Upload received. " : ""}Build job #${result.job_id} queued.`;
                notice.hidden = false;
            } catch (error) {
                if (controller.signal.aborted) return;
                notice.textContent = error.message || "Unable to add music. Please try again.";
                notice.hidden = false;
            } finally {
                uploading = false;
                form.removeAttribute("aria-busy");
                render();
            }
        });
        for (const [selector, input] of [["[data-add-pick-files]", filesInput], ["[data-add-pick-folder]", folderInput]]) {
            on(form.querySelector(selector), "click", () => input.click());
            on(input, "change", () => {
                collect(Array.from(input.files, (file) => ({ file, path: file.webkitRelativePath || file.name })));
                input.value = "";
            });
        }
        on(list, "click", (event) => {
            const button = event.target.closest("[data-remove-key]");
            if (!button) return;
            selected.delete(button.dataset.removeKey);
            render();
            (list.querySelector("button") || pick).focus();
        });
        on(clear, "click", () => {
            selected.clear();
            notice.hidden = true;
            render();
        });
        // Keep file drops anywhere in the form from navigating away from the app.
        on(form, "dragover", (event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
        });
        on(zone, "dragenter", (event) => {
            event.preventDefault();
            dragDepth++;
            zone.classList.add("is-dragging");
        });
        on(zone, "dragleave", () => {
            if (--dragDepth <= 0) {
                dragDepth = 0;
                zone.classList.remove("is-dragging");
            }
        });
        on(form, "drop", async (event) => {
            event.preventDefault();
            dragDepth = 0;
            zone.classList.remove("is-dragging");
            if (uploading || reading || mode.value !== "files") return;
            // Capture entries and files while the drop event's data store is readable.
            const items = Array.from(event.dataTransfer.items || [])
                .filter((item) => item.kind === "file")
                .map((item) => ({ entry: item.webkitGetAsEntry?.(), file: item.getAsFile() }));
            const fallback = Array.from(event.dataTransfer.files);
            reading = true;
            notice.hidden = true;
            form.setAttribute("aria-busy", "true");
            render();
            try {
                const files = [];
                if (items.length) {
                    for (const { entry, file } of items) {
                        if (entry) await readDroppedEntry(entry, "", files, controller.signal);
                        else if (file) files.push({ file, path: file.webkitRelativePath || file.name });
                    }
                } else {
                    for (const file of fallback) files.push({ file, path: file.webkitRelativePath || file.name });
                }
                controller.signal.throwIfAborted();
                collect(files);
            } catch (error) {
                if (controller.signal.aborted) return;
                notice.textContent = `Unable to read dropped files: ${error.message || "Please try again."}`;
                notice.hidden = false;
            } finally {
                reading = false;
                form.removeAttribute("aria-busy");
                if (!controller.signal.aborted) render();
            }
        });
        render();
        return () => {
            controller.abort();
            selected.clear();
        };
    });
}
