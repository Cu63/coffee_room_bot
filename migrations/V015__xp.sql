-- XP (опыт) пользователя в конкретном чате
CREATE TABLE user_xp (
    user_id     BIGINT  NOT NULL REFERENCES users(id),
    chat_id     BIGINT  NOT NULL,
    xp          BIGINT  NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id)
);

CREATE INDEX idx_user_xp_chat ON user_xp (chat_id, xp DESC);
