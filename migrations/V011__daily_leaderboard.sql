-- Флаг реплая на сообщении (для подсчёта лидера по реплаям)
ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_reply BOOLEAN NOT NULL DEFAULT FALSE;

-- Победы в угадайках (word / rword)
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS wins_word  INT NOT NULL DEFAULT 0;
ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS wins_rword INT NOT NULL DEFAULT 0;

-- Ежедневные победы в играх (для лидерборда за день)
CREATE TABLE IF NOT EXISTS daily_game_wins (
    user_id    BIGINT NOT NULL REFERENCES users(id),
    chat_id    BIGINT NOT NULL,
    date       DATE   NOT NULL,
    ttt_wins   INT    NOT NULL DEFAULT 0,
    word_wins  INT    NOT NULL DEFAULT 0,
    rword_wins INT    NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_game_wins_chat_date ON daily_game_wins (chat_id, date);

-- Индекс для быстрой выборки реплаев за день
CREATE INDEX IF NOT EXISTS idx_messages_chat_sent_reply ON messages (chat_id, sent_at, is_reply);
