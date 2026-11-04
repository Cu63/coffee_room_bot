-- Предупреждения модераторов (/warn): набрал warn.limit за warn.period_days — автомут
CREATE TABLE warnings (
    id          BIGSERIAL   PRIMARY KEY,
    chat_id     BIGINT      NOT NULL,
    user_id     BIGINT      NOT NULL REFERENCES users(id),   -- кому
    issued_by   BIGINT      NOT NULL REFERENCES users(id),   -- кто выписал
    reason      TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_warnings_chat_user ON warnings (chat_id, user_id, created_at DESC);
