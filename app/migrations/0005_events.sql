CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NULL,
    repeat TEXT NOT NULL DEFAULT 'never'
        CHECK (repeat IN ('never', 'weekly', 'monthly', 'yearly')),
    created_at TEXT NOT NULL
);
