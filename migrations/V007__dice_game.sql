-- Игра в кости между участниками чата
CREATE TABLE dice_games (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT      NOT NULL,
    message_id  BIGINT,                         -- id сообщения-анонса (заполняется после отправки)
    bet         INT         NOT NULL,           -- ставка каждого участника
    status      TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'finished')),
    ends_at     TIMESTAMPTZ NOT NULL,           -- когда заканчивается сбор участников
    created_by  BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dice_games_chat_status ON dice_games (chat_id, status);
CREATE INDEX idx_dice_games_expired     ON dice_games (ends_at) WHERE status = 'pending';

-- Участники (ставка списывается при вступлении)
CREATE TABLE dice_participants (
    game_id     INT         NOT NULL REFERENCES dice_games(id) ON DELETE CASCADE,
    user_id     BIGINT      NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (game_id, user_id)
);
