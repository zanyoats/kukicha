export default function registerAuth(up) {
    let navigating = false;

    function navigateToLogin(response) {
        const url = response.header("X-Kukicha-Login");
        if (!url || !response.ok) return false;
        if (!navigating) {
            navigating = true;
            window.location.assign(url);
        }
        return true;
    }

    // A successful logout redirects to the login document.
    up.on("up:request:loaded", (event) => navigateToLogin(event.response));

    // Stop login HTML from being inserted into the app or an overlay, including
    // responses served from Unpoly's cache without a new request event.
    up.on("up:fragment:loaded", (event) => {
        if (navigateToLogin(event.response)) event.preventDefault();
    });
}
