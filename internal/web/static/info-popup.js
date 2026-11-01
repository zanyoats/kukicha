export default function registerInfoPopup(up) {
    const gap = 16;
    const space = (origin) => {
        const bounds = origin.getBoundingClientRect();
        return {
            top: Math.max(0, bounds.top - gap),
            bottom: Math.max(0, document.documentElement.clientHeight - bounds.bottom - gap),
        };
    };
    const isInfoPopup = (options) => options.mode === "popup" &&
        String(options.class || "").split(/\s+/).includes("info-popup");

    up.on("up:layer:open", (event) => {
        if (!event.origin || !isInfoPopup(event.layerOptions)) return;
        // Opening help must not reveal the layer by scrolling its parent page.
        event.layerOptions.scroll = false;
        const available = space(event.origin);
        event.layerOptions.position = available.bottom >= available.top ? "bottom" : "top";
    });

    // Compilers run before Unpoly's opening focus and scroll handling.
    up.compiler("up-popup.info-popup .job-help", (help) => {
        const layer = up.layer.get(help);
        const popup = layer.element;
        if (!layer.origin) return;
        help.setAttribute("tabindex", "0");
        const update = () => {
            if (!popup.isConnected) return;
            const available = space(layer.origin);
            popup.style.setProperty("--info-popup-max-height", `${available[layer.position]}px`);
            layer.sync();
        };
        const stopResize = up.on(window, "resize", update);
        const stopScroll = up.on(document, "scroll", { capture: true }, (event) => {
            // Internal help scrolling must not resize or reposition its popup.
            if (!popup.contains(event.target)) update();
        });
        update();
        return [stopResize, stopScroll];
    });
}
