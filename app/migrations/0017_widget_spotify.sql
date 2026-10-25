CREATE TABLE widgets_new (
    id INTEGER PRIMARY KEY,
    screen_id INTEGER NOT NULL REFERENCES tv_screens(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('clock', 'weather', 'calendar', 'lists', 'spotify')),
    list_id INTEGER NULL REFERENCES lists(id) ON DELETE CASCADE,
    w INTEGER NOT NULL CHECK (w IN (1, 2)),
    h INTEGER NOT NULL CHECK (h IN (1, 2)),
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    col INTEGER NOT NULL DEFAULT 0,
    row INTEGER NOT NULL DEFAULT 0
);

INSERT INTO widgets_new (id, screen_id, kind, list_id, w, h, sort_order, created_at, col, row)
SELECT id, screen_id, kind, list_id, w, h, sort_order, created_at, col, row FROM widgets;

DROP TABLE widgets;

ALTER TABLE widgets_new RENAME TO widgets;

CREATE INDEX widgets_screen_id ON widgets (screen_id);
