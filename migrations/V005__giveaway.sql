-- Розыгрыши
CREATE TABLE giveaways (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT      NOT NULL,
    message_id  BIGINT,                         -- id сообщения-анонса (заполняется после отправки)
    created_by  BIGINT      NOT NULL,
    prizes      INTEGER[]   NOT NULL,            -- массив призов, напр. {500,100,50}
    status      TEXT        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'finished')),
    ends_at     TIMESTAMPTZ,                     -- NULL = только ручное завершение
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_giveaways_chat_status ON giveaways (chat_id, status);
CREATE INDEX idx_giveaways_expired     ON giveaways (ends_at) WHERE status = 'active' AND ends_at IS NOT NULL;

-- Участники
CREATE TABLE giveaway_participants (
    giveaway_id INTEGER     NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id     BIGINT      NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (giveaway_id, user_id)
);

-- Победители (заполняется при завершении)
CREATE TABLE giveaway_winners (
    giveaway_id INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id     BIGINT  NOT NULL,
    prize       INTEGER NOT NULL,
    position    INTEGER NOT NULL,   -- 1-based, 1 = главный приз
    PRIMARY KEY (giveaway_id, position)
);