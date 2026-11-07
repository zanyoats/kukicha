export default function registerListening(up) {
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
        }).catch(() => {
            if (disposed) return;
            element.textContent = "Unable to display the chart. Monthly values are available below.";
            element.parentElement.querySelector(".listening-table").classList.remove("visually-hidden");
            element.setAttribute("role", "status");
        });
        return () => { disposed = true; chart?.destroy(); };
    });
}
