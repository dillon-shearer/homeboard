CREATE TABLE widgets (
    id INTEGER PRIMARY KEY,
    screen_id INTEGER NOT NULL REFERENCES tv_screens(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('clock', 'weather', 'calendar', 'lists')),
    list_id INTEGER NULL REFERENCES lists(id) ON DELETE CASCADE,
    w INTEGER NOT NULL CHECK (w IN (1, 2)),
    h INTEGER NOT NULL CHECK (h IN (1, 2)),
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX widgets_screen_id ON widgets (screen_id);

ALTER TABLE lists ADD COLUMN tv_open INTEGER NOT NULL DEFAULT 1;

DELETE FROM tv_screens;

INSERT INTO tv_screens (name, sort_order, created_at)
VALUES
    ('Daily Brief', 10, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
    ('Calendar', 20, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
    ('Notes', 30, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));

INSERT INTO widgets (screen_id, kind, list_id, w, h, sort_order, created_at)
SELECT s.id, v.kind, v.list_id, v.w, v.h, v.sort_order, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
FROM tv_screens s
JOIN (
    SELECT 'Daily Brief' AS screen, 'clock' AS kind, NULL AS list_id, 1 AS w, 1 AS h, 10 AS sort_order
    UNION ALL SELECT 'Daily Brief', 'weather', NULL, 1, 1, 20
    UNION ALL SELECT 'Daily Brief', 'calendar', NULL, 1, 1, 30
    UNION ALL SELECT 'Daily Brief', 'lists', (SELECT id FROM lists ORDER BY sort_order, id LIMIT 1), 1, 1, 40
    UNION ALL SELECT 'Calendar', 'calendar', NULL, 2, 2, 10
    UNION ALL SELECT 'Notes', 'lists', NULL, 2, 2, 10
) v ON v.screen = s.name;

CREATE TABLE settings_new (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    interval_s INTEGER DEFAULT 20,
    transition INTEGER DEFAULT 1,
    fit TEXT DEFAULT 'contain',
    shuffle INTEGER DEFAULT 0,
    captions INTEGER DEFAULT 0,
    updated_at TEXT,
    current_photo_id INTEGER NULL,
    channel TEXT NOT NULL DEFAULT 'photos' CHECK (channel IN ('photos', 'screen')),
    channel_timeout_min INTEGER NOT NULL DEFAULT 5,
    screen_id INTEGER REFERENCES tv_screens(id) ON DELETE SET NULL,
    clock_size INTEGER NOT NULL DEFAULT 5,
    caption_size INTEGER NOT NULL DEFAULT 5,
    clock_pos TEXT NOT NULL DEFAULT 'bottom-right',
    caption_pos TEXT NOT NULL DEFAULT 'bottom-left',
    clock_on_top INTEGER NOT NULL DEFAULT 1,
    weather_location TEXT NOT NULL DEFAULT '30263',
    weather_lat REAL NULL,
    weather_lon REAL NULL,
    weather_name TEXT NULL
);

INSERT INTO settings_new (
    id, interval_s, transition, fit, shuffle, captions, updated_at, current_photo_id,
    channel, channel_timeout_min, screen_id,
    clock_size, caption_size, clock_pos, caption_pos, clock_on_top
)
SELECT
    id, interval_s, transition, fit, shuffle, captions, updated_at, current_photo_id,
    CASE channel WHEN 'photos' THEN 'photos' ELSE 'screen' END,
    channel_timeout_min,
    CASE channel
        WHEN 'calendar' THEN (SELECT id FROM tv_screens WHERE name = 'Calendar')
        WHEN 'lists' THEN (SELECT id FROM tv_screens WHERE name = 'Notes')
    END,
    clock_size, caption_size, clock_pos, caption_pos, clock_on_top
FROM settings;

DROP TABLE settings;

ALTER TABLE settings_new RENAME TO settings;
