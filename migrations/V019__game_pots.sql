-- Банк пошаговой игры: ставки лежат здесь, пока игру не рассчитают.
--
-- Ставка списывается с игрока и кладётся в банк игры (ref: bj:<id>, ttt:<id>,
-- duel:<invite_id>), выигрыш и возвраты берутся из банка. Выплатить больше, чем
-- положили, нельзя: второй расчёт той же игры упрётся в пустой банк, а не начислит
-- деньги из воздуха. Банк с деньгами, который сутки не трогали, — потерянная игра:
-- о нём сообщает ежедневная сверка (ledger_audit_loop).
CREATE TABLE game_pots (
    chat_id    BIGINT      NOT NULL,
    ref        TEXT        NOT NULL,
    amount     BIGINT      NOT NULL CHECK (amount >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, ref)
);

CREATE INDEX game_pots_updated_at_idx ON game_pots (updated_at);
