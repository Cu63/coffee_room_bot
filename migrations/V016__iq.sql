-- IQ пользователя в конкретном чате.
-- Стартовое значение у всех — 89 (задаётся через config.iq.default при вставке).
-- IQ может уходить в минус — «терять можно сколько угодно».
CREATE TABLE user_iq (
    user_id     BIGINT  NOT NULL REFERENCES users(id),
    chat_id     BIGINT  NOT NULL,
    iq          BIGINT  NOT NULL DEFAULT 89,
    PRIMARY KEY (user_id, chat_id)
);

CREATE INDEX idx_user_iq_chat ON user_iq (chat_id, iq DESC);
