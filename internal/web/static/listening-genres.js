// Canonical taxonomy names keep colors stable across database rebuilds.
const genreColors = new Map([
    ["Blues", "--genre-blues"],
    ["Brass & Military", "--genre-brass-military"],
    ["Children's", "--genre-childrens"],
    ["Classical", "--genre-classical"],
    ["Electronic", "--genre-electronic"],
    ["Folk, World, & Country", "--genre-folk-world-country"],
    ["Funk / Soul", "--genre-funk-soul"],
    ["Hip Hop", "--genre-hip-hop"],
    ["Jazz", "--genre-jazz"],
    ["Latin", "--genre-latin"],
    ["Non-Music", "--genre-non-music"],
    ["Pop", "--genre-pop"],
    ["Reggae", "--genre-reggae"],
    ["Rock", "--genre-rock"],
    ["Stage & Screen", "--genre-stage-screen"],
]);

export default function registerListeningGenres(up) {
    up.compiler("[data-listening-genres]", (element, { genres, unknownItem }) => {
        const colors = new Map(genreColors);
        colors.set(unknownItem, "--genre-unknown");
        let disposed = false;
        let chart;
        import("./chart.umd.min.js").then(() => {
            if (disposed) return;
            const style = getComputedStyle(element);
            const color = (name) => style.getPropertyValue(name).trim();
            chart = new window.Chart(element.querySelector("canvas"), {
                type: "line",
                data: {
                    labels: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                    datasets: genres.map((genre) => {
                        const genreColor = color(colors.get(genre.name) ?? "--genre-unknown");
                        return {
                            label: genre.name,
                            data: genre.counts,
                            backgroundColor: genreColor,
                            borderColor: genreColor,
                            borderWidth: 1,
                            fill: "stack",
                            stack: "genres",
                            tension: 0,
                            spanGaps: false,
                            pointRadius: 2,
                            pointHoverRadius: 4,
                        };
                    }),
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: matchMedia("(prefers-reduced-motion: reduce)").matches ? false : undefined,
                    color: color("--text-secondary"),
                    font: { family: style.fontFamily },
                    interaction: { mode: "index", intersect: false },
                    scales: {
                        x: {
                            grid: { display: false },
                            ticks: { color: color("--text-secondary"), autoSkip: false, maxRotation: 0 },
                        },
                        y: {
                            stacked: true,
                            beginAtZero: true,
                            suggestedMax: genres.length ? undefined : 1,
                            title: { display: true, text: "Plays", color: color("--text-secondary") },
                            grid: { color: color("--border") },
                            ticks: { precision: 0, color: color("--text-secondary") },
                        },
                    },
                    plugins: {
                        legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12 } },
                        tooltip: { callbacks: {
                            label: (item) => `${item.dataset.label}: ${item.raw.toLocaleString()} plays`,
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
            element.textContent = "Unable to display the chart. Genre values are available below.";
            element.parentElement.querySelector(".listening-table").classList.remove("visually-hidden");
            element.setAttribute("role", "status");
        });
        return () => { disposed = true; chart?.destroy(); };
    });
}
