-- Активный режим чата (silence / gif)
CREATE TABLE IF NOT EXISTS chatmode (
    chat_id      BIGINT      NOT NULL PRIMARY KEY,
    mode         TEXT        NOT NULL,               -- 'silence' | 'gif'
    activated_by BIGINT      NOT NULL REFERENCES users(id),
    activated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    -- снапшот прав чата ДО активации (JSON)
    saved_perms  JSONB       NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chatmode_expires ON chatmode (expires_at);
