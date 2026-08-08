INSERT INTO taxonomy (
    id,
    kind,
    name,
    parent,
    source,
    alias,
    status,
    count
)
VALUES (
    :id,
    :kind,
    :name,
    :parent,
    :source,
    :alias,
    :status,
    :count
);
