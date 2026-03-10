CREATE TABLE saved_permissions (
    user_id     BIGINT  NOT NULL,
    chat_id     BIGINT  NOT NULL,
    permissions JSONB   NOT NULL,
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, chat_id)
);