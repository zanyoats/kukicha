import { requestJSON } from "./json-api.js";
import { showToast } from "./notifications.js";

export default function registerRoots(up) {
    const refresh = (element) => {
        const page = element.closest(".roots-page");
        const layer = up.layer.get(element);
        return up.render({
            url: page ? page.dataset.rootsUrl : element.dataset.rootsUrl,
            target: ".config-page",
            layer: page ? layer : layer.parent,
            history: false,
            cache: false,
        });
    };

    up.compiler("[data-drop-root]", (button) => {
        let destroyed = false;
        const unbind = up.on(button, "click", async () => {
            if (button.disabled) return;
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            try {
                await requestJSON(up, button.dataset.dropRoot, {
                    method: "POST",
                    body: { config_id: button.dataset.rootId },
                    errorMessage: "Unable to remove the storage location.",
                });
                if (destroyed) return;
                showToast("Storage location marked for removal. Rebuild to remove its library data. Files in storage are left untouched.", { trigger: button });
                await refresh(button);
            } catch (failure) {
                if (!destroyed) showToast(failure.message, { role: "alert", trigger: button });
            } finally {
                if (!destroyed) {
                    button.disabled = false;
                    button.removeAttribute("aria-busy");
                }
            }
        });
        return [() => { destroyed = true; }, unbind];
    });

    up.compiler("[data-root-form]", (form) => {
        const type = form.elements.type;
        const button = form.querySelector('[type="submit"]');
        const error = form.querySelector("[data-root-error]");
        let destroyed = false;
        let submitting = false;
        const markInvalid = () => form.classList.add("validation-attempted");
        form.addEventListener("invalid", markInvalid, true);
        const update = () => {
            for (const panel of form.querySelectorAll("[data-root-panel]")) {
                const active = panel.dataset.rootPanel === type.value;
                panel.hidden = !active;
                for (const field of panel.querySelectorAll("input, select")) field.disabled = !active;
            }
        };
        update();
        const change = up.on(type, "change", update);
        const submit = up.on(form, "submit", async (event) => {
            event.preventDefault();
            if (submitting || !form.reportValidity()) return;
            submitting = true;
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            error.hidden = true;
            try {
                await requestJSON(up, form.action, {
                    method: form.dataset.method,
                    body: Object.fromEntries(new FormData(form)),
                    errorMessage: "Unable to save the storage location.",
                });
                if (destroyed) return;
                showToast(form.dataset.method === "PUT" ? "Storage location saved." : "Storage location added. You can now import music.", { trigger: button });
                await refresh(form);
            } catch (failure) {
                if (!destroyed) {
                    error.textContent = failure.message;
                    error.hidden = false;
                }
            } finally {
                submitting = false;
                if (!destroyed) {
                    button.disabled = false;
                    button.removeAttribute("aria-busy");
                }
            }
        });
        return [
            () => { destroyed = true; },
            () => form.removeEventListener("invalid", markInvalid, true),
            change,
            submit,
        ];
    });
}
