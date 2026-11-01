// Group file paths by their full containing directory, including the trailing
// slash. Paths use "/" separators; bare filenames belong to the "" directory.
// Preserve first-seen directory order and item order within each directory.
export function groupByDirectory(items, pathOf = (item) => item) {
    const groups = new Map();
    for (const item of items) {
        const path = pathOf(item);
        const directory = path.slice(0, path.lastIndexOf("/") + 1);
        if (!groups.has(directory)) groups.set(directory, []);
        groups.get(directory).push(item);
    }
    return groups;
}
