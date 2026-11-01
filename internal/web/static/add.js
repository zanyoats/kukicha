function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4);
    return `${(bytes / 1024 ** unit).toFixed(1)} ${["B", "KB", "MB", "GB", "TB"][unit]}`;
}

export default function registerAdd(up) {
    up.compiler("[data-add-form]", (form) => {
        const filesInput = form.querySelector("[data-add-files]");
        const zone = form.querySelector("[data-add-dropzone]");
        const list = form.querySelector("[data-add-list]");
        const status = form.querySelector("[data-add-status]");
        const notice = form.querySelector("[data-add-notice]");
        const clear = form.querySelector("[data-add-clear]");
        const extensions = filesInput.accept.split(",");
        const selected = new Map();
        const controller = new AbortController();
        let dragDepth = 0;
        const on = (element, event, callback) => element.addEventListener(event, callback, { signal: controller.signal });
        const render = () => {
            const fragment = document.createDocumentFragment();
            let bytes = 0;
            for (const [key, file] of selected) {
                bytes += file.size;
                const row = document.createElement("li");
                const name = document.createElement("span");
                name.textContent = file.name;
                const size = document.createElement("span");
                size.className = "text-secondary";
                size.textContent = formatSize(file.size);
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "action-button";
                remove.textContent = "Remove";
                remove.setAttribute("aria-label", `Remove ${file.name}`);
                remove.dataset.removeKey = key;
                row.append(name, size, remove);
                fragment.append(row);
            }
            list.replaceChildren(fragment);
            list.hidden = selected.size === 0;
            clear.disabled = selected.size === 0;
            status.textContent = selected.size
                ? `${selected.size} ${selected.size === 1 ? "file" : "files"} selected · ${formatSize(bytes)}`
                : "No files selected.";
        };
        const collect = (files, skippedFolders = 0) => {
            let skipped = 0;
            for (const file of files) {
                if (!extensions.some((extension) => file.name.toLowerCase().endsWith(extension))) {
                    skipped++;
                    continue;
                }
                const key = JSON.stringify([file.name, file.size, file.lastModified]);
                selected.set(key, file);
            }
            notice.textContent = [
                skipped ? `${skipped} unsupported ${skipped === 1 ? "file was" : "files were"} skipped.` : "",
                skippedFolders ? "Folders are not supported. Create a ZIP or TAR archive first." : "",
            ].filter(Boolean).join(" ");
            notice.hidden = !notice.textContent;
            render();
        };
        on(form, "submit", (event) => event.preventDefault());
        on(form.querySelector("[data-add-pick-files]"), "click", () => filesInput.click());
        on(filesInput, "change", () => {
            collect(filesInput.files);
            filesInput.value = "";
        });
        on(list, "click", (event) => {
            const button = event.target.closest("[data-remove-key]");
            if (!button) return;
            selected.delete(button.dataset.removeKey);
            render();
            (list.querySelector("button") || form.querySelector("[data-add-pick-files]")).focus();
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
        on(form, "drop", (event) => {
            event.preventDefault();
            dragDepth = 0;
            zone.classList.remove("is-dragging");
            const items = Array.from(event.dataTransfer.items || [])
                .filter((item) => item.kind === "file");
            const files = [];
            let folders = 0;
            for (const item of items) {
                if (item.webkitGetAsEntry?.()?.isDirectory) {
                    folders++;
                    continue;
                }
                const file = item.getAsFile();
                if (file) files.push(file);
            }
            collect(items.length ? files : event.dataTransfer.files, folders);
        });
        return () => {
            controller.abort();
            selected.clear();
        };
    });
}
