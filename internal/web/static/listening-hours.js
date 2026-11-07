export default function registerListeningHours(up) {
    up.compiler("[data-listening-hours]", (element, counts) => {
        let disposed = false;
        let chart;
        import("./chart.umd.min.js").then(() => {
            if (disposed) return;
            const style = getComputedStyle(element);
            const color = (name) => style.getPropertyValue(name).trim();
            const hourLabel = (hour) => `${hour % 12 || 12} ${hour < 12 ? "AM" : "PM"}`;
            chart = new window.Chart(element.querySelector("canvas"), {
                type: "polarArea",
                data: {
                    labels: Array.from({ length: 24 }, (_, hour) => hourLabel(hour)),
                    datasets: [{
                        data: counts,
                        backgroundColor: `color-mix(in srgb, ${color("--text-muted")} 55%, transparent)`,
                        hoverBackgroundColor: `color-mix(in srgb, ${color("--text-primary")} 65%, transparent)`,
                        borderColor: color("--surface"),
                        borderWidth: 1,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: matchMedia("(prefers-reduced-motion: reduce)").matches ? false : undefined,
                    color: color("--text-secondary"),
                    font: { family: style.fontFamily },
                    scales: {
                        r: {
                            beginAtZero: true,
                            startAngle: -7.5,
                            suggestedMax: counts.some((count) => count > 0) ? undefined : 1,
                            grid: { color: color("--border") },
                            angleLines: { display: false },
                            ticks: { z: 1, precision: 0, maxTicksLimit: 4, color: color("--text-secondary"), backdropColor: color("--surface") },
                            pointLabels: {
                                display: true,
                                centerPointLabels: true,
                                color: color("--text-secondary"),
                                callback: (label, index) => index % 3 === 0 ? label : "",
                            },
                        },
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: {
                            title: (items) => {
                                if (!items.length) return "";
                                const hour = items[0].dataIndex;
                                return `${hourLabel(hour)}–${hourLabel((hour + 1) % 24)}`;
                            },
                            label: (item) => `${item.raw.toLocaleString()} scrobbles`,
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
            element.textContent = "Unable to display the chart. Hourly values are available below.";
            element.parentElement.querySelector(".listening-table").classList.remove("visually-hidden");
            element.setAttribute("role", "status");
        });
        return () => { disposed = true; chart?.destroy(); };
    });
}
