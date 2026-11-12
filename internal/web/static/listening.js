export default function registerListening(up) {
    const browserTimezone = () => {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch {
            return undefined;
        }
    };
    const withBrowserTimezone = (href) => {
        const url = new URL(href, window.location.href);
        if (url.searchParams.get("tz")) return null;
        const timezone = browserTimezone();
        if (!timezone) return null;
        url.searchParams.set("tz", timezone);
        return url.href;
    };
    up.compiler("[data-listening-link]", (link) => {
        const url = withBrowserTimezone(link.href);
        if (url) link.href = url;
    });
    // Direct visits have no browser timezone until JavaScript runs. Reload the
    // whole page so the year selector, cutoff and every fragment agree.
    up.compiler("[data-listening-url]", (page) => {
        const url = withBrowserTimezone(page.dataset.listeningUrl);
        if (url) window.location.replace(url);
    });

    up.compiler("[data-listening-chart]", (element, data) => {
        let disposed = false;
        let chart;
        // Load the vendored library only when the listening chart is inserted.
        import("./chart.umd.min.js").then(() => {
            if (disposed) return;
            const style = getComputedStyle(element);
            const color = (name) => style.getPropertyValue(name).trim();
            chart = new window.Chart(element.querySelector("canvas"), {
                type: "bar",
                data: {
                    labels: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                    datasets: [
                        { label: String(data.year), data: data.current, backgroundColor: color("--text-primary") },
                        { label: String(data.year - 1), data: data.previous, backgroundColor: color("--text-muted") },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: matchMedia("(prefers-reduced-motion: reduce)").matches ? false : undefined,
                    color: color("--text-secondary"),
                    font: { family: style.fontFamily },
                    scales: {
                        x: { grid: { display: false }, border: { display: false }, ticks: { color: color("--text-secondary"), autoSkip: false, maxRotation: 0 } },
                        y: { beginAtZero: true, display: false },
                    },
                    plugins: {
                        legend: { position: "top", align: "end", labels: { boxWidth: 12, boxHeight: 12 } },
                        tooltip: { callbacks: {
                            title: (items) => items.length ? `${items[0].label} ${items[0].dataset.label}` : "",
                            label: (item) => `${item.parsed.y.toLocaleString()} scrobbles`,
                        } },
                    },
                },
            });
            element.removeAttribute("aria-busy");
            element.querySelector(".listening-chart-loading")?.remove();
            // Give the initial transparent canvas a frame before fading it in.
            requestAnimationFrame(() => requestAnimationFrame(() => {
                if (!disposed) element.classList.add("is-ready");
            }));
        }).catch(() => {
            if (disposed) return;
            element.removeAttribute("aria-busy");
            element.textContent = "Unable to display the chart. Monthly values are available below.";
            element.parentElement.querySelector(".listening-table").classList.remove("visually-hidden");
            element.setAttribute("role", "status");
        });
        return () => { disposed = true; chart?.destroy(); };
    });
}
