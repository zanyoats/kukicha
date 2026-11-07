// Returns the Unpoly response without parsing its body, including empty 204 responses.
export async function requestJSON(
    up,
    url,
    {
        method,
        body,
        timeout,
        errorMessage = "Unable to complete the request. Please try again.",
    },
) {
    try {
        return await up.request(url, {
            method,
            timeout,
            wrapMethod: false,
            contentType: "application/json",
            payload: JSON.stringify(body),
        });
    } catch (failure) {
        let message = errorMessage;
        try {
            const body = JSON.parse(failure.text);
            if (typeof body.error === "string" && body.error.trim()) {
                message = body.error;
            }
        } catch {
            // Network failures and non-JSON responses use the caller's fallback.
        }
        throw new Error(message, { cause: failure });
    }
}
