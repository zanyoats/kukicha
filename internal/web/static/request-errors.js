import { showToast } from "./notifications.js";

const reasons = new Map([
    [400, "Bad request"],
    [401, "Sign-in required"],
    [403, "Access denied"],
    [404, "Page not found"],
    [405, "Method not allowed"],
    [409, "Conflict"],
    [410, "Page no longer available"],
    [429, "Too many requests"],
    [500, "Internal server error"],
    [502, "Bad gateway"],
    [503, "Service unavailable"],
    [504, "Gateway timeout"],
]);

function isHTTPError(response) {
    return response.status >= 400 && response.status < 600;
}

function responseMessage(response) {
    const type = response.header("Content-Type")?.split(";")[0].trim().toLowerCase();
    if (type === "text/plain") return response.text.trim();
    if (type === "application/json") {
        try {
            const message = JSON.parse(response.text)?.error;
            if (typeof message === "string") return message.trim();
        } catch {
            // Keep the status label if the response body is malformed.
        }
    }
    return "";
}

export default function registerRequestErrors(up) {
    // Unpoly normally skips up:fragment:loaded for empty response bodies.
    const skipResponse = up.fragment.config.skipResponse;
    up.fragment.config.skipResponse = (options) =>
        !isHTTPError(options.response) && skipResponse(options);

    up.on("up:fragment:loaded", (event) => {
        const { request, response, renderOptions } = event;
        if (!isHTTPError(response)) return;
        event.preventDefault();

        const status = response.status;
        const reason = reasons.get(status) ?? (status >= 500 ? "Server error" : "Request failed");
        const message = responseMessage(response);
        const origin = renderOptions.origin;
        const link = origin?.closest("a[href]");
        // Back and Forward restore a history location without a triggering link.
        const restoringPage = !origin && renderOptions.history === true;
        const followingLink = link && !link.matches('[role="button"]');
        if (request.method !== "GET" || !(followingLink || restoringPage)) {
            const summary = `${reason} (HTTP ${status}).`;
            showToast(message ? `${summary} ${message}` : summary, { role: "alert", trigger: origin });
            return;
        }

        const main = document.querySelector("#main-content") ?? document.querySelector("main[up-main]");
        const fragment = main.cloneNode(false);
        const section = document.createElement("section");
        section.className = "content-inner-shell";
        const heading = document.createElement("h1");
        heading.textContent = `${status} · ${reason}`;
        section.append(heading);
        if (message) {
            const detail = document.createElement("p");
            detail.className = "request-error-message";
            detail.textContent = message;
            section.append(detail);
        }
        const login = response.header("X-Kukicha-Login");
        if (status === 401 && login) {
            const signIn = document.createElement("a");
            signIn.href = login;
            signIn.textContent = "Sign in";
            section.append(signIn);
        }
        fragment.append(section);
        up.render({
            target: main, fragment, layer: "root", peel: true,
            history: true, location: request.url, source: request.url, title: heading.textContent,
            focus: "main", scroll: "top",
        });
    });
}
