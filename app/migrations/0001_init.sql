CREATE TABLE photos (
    id INTEGER PRIMARY KEY,
    filename TEXT UNIQUE NOT NULL,
    thumb TEXT NOT NULL,
    caption TEXT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT NULL
);

CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    interval_s INTEGER DEFAULT 20,
    transition INTEGER DEFAULT 1,
    fit TEXT DEFAULT 'contain',
    shuffle INTEGER DEFAULT 0,
    captions INTEGER DEFAULT 0,
    updated_at TEXT
);

INSERT INTO settings (id) VALUES (1);
