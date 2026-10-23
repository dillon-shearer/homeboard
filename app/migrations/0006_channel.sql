ALTER TABLE settings ADD COLUMN channel TEXT NOT NULL DEFAULT 'photos'
    CHECK (channel IN ('photos', 'calendar', 'lists'));
ALTER TABLE settings ADD COLUMN channel_timeout_min INTEGER NOT NULL DEFAULT 5;
