CREATE TABLE active_mutes (
    user_id             BIGINT      NOT NULL,
    chat_id             BIGINT      NOT NULL,
    muted_by            BIGINT      NOT NULL REFERENCES users(id),
    until_at            TIMESTAMPTZ NOT NULL,
    was_admin           BOOLEAN     NOT NULL DEFAULT FALSE,
    admin_permissions   JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, chat_id)
);

CREATE INDEX idx_active_mutes_until ON active_mutes (until_at);