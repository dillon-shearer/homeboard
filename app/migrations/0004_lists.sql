CREATE TABLE lists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'checklist' CHECK (kind IN ('checklist', 'notes')),
    show_on_tv INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE list_items (
    id INTEGER PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX list_items_list_id ON list_items (list_id);

INSERT INTO lists (name, kind, sort_order, created_at)
VALUES
    ('Publix', 'checklist', 10, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
    ('BJs', 'checklist', 20, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));
