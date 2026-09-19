export default function registerArtwork(up) {
    up.compiler(".album-card-artwork, .album-artwork, [data-player-artwork]", (image) => {
        const fallbackURL = new URL(image.dataset.artworkFallbackUrl, document.baseURI).href;
        const reveal = () => image.classList.remove("is-loading");
        const showFallback = () => {
            if (image.getAttribute("src") && image.src !== fallbackURL) {
                image.src = fallbackURL;
            }
            reveal();
        };
        let source;
        const prepare = () => {
            const nextSource = image.getAttribute("src");
            if (nextSource === source) return;
            source = nextSource;

            // Empty footer artwork stays hidden; cached images appear immediately.
            if (!source || image.complete) {
                reveal();
                if (source && !image.naturalWidth) showFallback();
            } else {
                image.classList.add("is-loading");
            }
        };

        image.addEventListener("load", reveal);
        image.addEventListener("error", showFallback);
        // The kept footer reuses its image when the current track changes.
        const observer = new MutationObserver(prepare);
        if (image.hasAttribute("data-player-artwork")) {
            observer.observe(image, { attributes: true, attributeFilter: ["src"] });
        }
        prepare();

        return () => {
            observer.disconnect();
            image.removeEventListener("load", reveal);
            image.removeEventListener("error", showFallback);
        };
    });
}
