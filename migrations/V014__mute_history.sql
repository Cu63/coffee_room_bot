-- История применённых мутов (для дневной статистики)
CREATE TABLE mute_history (
    id          BIGSERIAL   PRIMARY KEY,
    chat_id     BIGINT      NOT NULL,
    user_id     BIGINT      NOT NULL REFERENCES users(id),   -- кого замутили
    muted_by    BIGINT      NOT NULL REFERENCES users(id),   -- кто выписал
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_mute_history_chat_created ON mute_history (chat_id, created_at DESC);
