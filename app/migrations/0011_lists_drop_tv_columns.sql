CREATE TABLE lists_new (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'checklist' CHECK (kind IN ('checklist', 'notes')),
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    tv_open INTEGER NOT NULL DEFAULT 1
);

INSERT INTO lists_new (id, name, kind, sort_order, created_at, tv_open)
SELECT id, name, kind, sort_order, created_at, tv_open FROM lists;

DROP TABLE lists;

ALTER TABLE lists_new RENAME TO lists;
