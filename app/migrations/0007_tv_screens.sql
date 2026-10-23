CREATE TABLE tv_screens (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

ALTER TABLE lists ADD COLUMN screen_id INTEGER REFERENCES tv_screens(id) ON DELETE SET NULL;

ALTER TABLE settings ADD COLUMN lists_screen_id INTEGER REFERENCES tv_screens(id) ON DELETE SET NULL;

INSERT INTO tv_screens (name, sort_order, created_at)
VALUES ('Lists', 10, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));

UPDATE lists SET screen_id = (SELECT MIN(id) FROM tv_screens) WHERE show_on_tv = 1;
