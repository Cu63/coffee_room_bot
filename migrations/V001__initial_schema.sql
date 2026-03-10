-- Пользователи
CREATE TABLE users (
    id          BIGINT      PRIMARY KEY,
    username    TEXT,
    full_name   TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Счёт пользователя в конкретном чате
CREATE TABLE scores (
    user_id     BIGINT  NOT NULL REFERENCES users(id),
    chat_id     BIGINT  NOT NULL,
    value       INT     NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id)
);

-- Отслеживание сообщений (автор + время) для проверки реакций
CREATE TABLE messages (
    message_id  BIGINT      NOT NULL,
    chat_id     BIGINT      NOT NULL,
    user_id     BIGINT      NOT NULL REFERENCES users(id),
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, chat_id)
);

-- История событий начисления / отмены
CREATE TABLE score_events (
    id          BIGSERIAL   PRIMARY KEY,
    chat_id     BIGINT      NOT NULL,
    actor_id    BIGINT      NOT NULL REFERENCES users(id),
    target_id   BIGINT      NOT NULL REFERENCES users(id),
    message_id  BIGINT      NOT NULL,
    emoji       TEXT        NOT NULL,
    delta       INT         NOT NULL,
    direction   TEXT        NOT NULL CHECK (direction IN ('ADD', 'REMOVE')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (actor_id, message_id, emoji)
);

CREATE INDEX idx_score_events_chat_created ON score_events (chat_id, created_at DESC);
CREATE INDEX idx_score_events_target       ON score_events (target_id, chat_id);

-- Дневные лимиты
CREATE TABLE daily_limits (
    user_id           BIGINT  NOT NULL REFERENCES users(id),
    chat_id           BIGINT  NOT NULL,
    date              DATE    NOT NULL,
    reactions_given   INT     NOT NULL DEFAULT 0,
    score_received    INT     NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id, date)
);
