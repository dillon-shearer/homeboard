ALTER TABLE photos ADD COLUMN source_sha256 TEXT NULL;
CREATE INDEX photos_source_sha256 ON photos (source_sha256);
