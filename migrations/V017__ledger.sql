-- Журнал операций с баллами.
--
-- Каждое изменение scores.value записывает триггер, поэтому журнал полон, даже
-- если баланс поправили мимо кода: ручным SQL или старой версией бота во время
-- деплоя. Тип операции, ссылку на объект и вторую сторону код передаёт через
-- настройки транзакции ledger.kind / ledger.ref / ledger.counterparty
-- (см. PostgresScoreRepository). Без них kind = 'unknown' — такие строки
-- показывает ночная сверка.

CREATE TABLE ledger (
    id              BIGSERIAL   PRIMARY KEY,
    chat_id         BIGINT      NOT NULL,
    user_id         BIGINT      NOT NULL,
    delta           BIGINT      NOT NULL,
    balance_after   BIGINT      NOT NULL,
    kind            TEXT        NOT NULL,   -- reaction, transfer, purchase, stake, prize, refund, bonus, tax, admin, opening
    ref             TEXT,                   -- что именно: msg:<id>, mute, bj:<game_id>, lot:<lot_id>…
    counterparty_id BIGINT,                 -- вторая сторона: кто поставил реакцию, кому перевод, кого замутили
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ledger_chat_user ON ledger (chat_id, user_id, created_at DESC);
CREATE INDEX idx_ledger_chat_created ON ledger (chat_id, created_at DESC);
CREATE INDEX idx_ledger_unknown ON ledger (created_at) WHERE kind = 'unknown';

CREATE FUNCTION ledger_record_score_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_chat  BIGINT;
    v_user  BIGINT;
    v_delta BIGINT;
    v_after BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_chat := OLD.chat_id; v_user := OLD.user_id; v_delta := -OLD.value; v_after := 0;
    ELSIF TG_OP = 'UPDATE' THEN
        v_chat := NEW.chat_id; v_user := NEW.user_id; v_delta := NEW.value - OLD.value; v_after := NEW.value;
    ELSE
        v_chat := NEW.chat_id; v_user := NEW.user_id; v_delta := NEW.value; v_after := NEW.value;
    END IF;

    IF v_delta <> 0 THEN
        INSERT INTO ledger (chat_id, user_id, delta, balance_after, kind, ref, counterparty_id)
        VALUES (
            v_chat, v_user, v_delta, v_after,
            COALESCE(NULLIF(current_setting('ledger.kind', true), ''), 'unknown'),
            NULLIF(current_setting('ledger.ref', true), ''),
            NULLIF(current_setting('ledger.counterparty', true), '')::BIGINT
        );
    END IF;
    RETURN NULL;
END;
$$;

-- Пока журнал заводится, баллы никто не меняет: стартовые остатки точные,
-- а всё, что запишут после миграции, поймает триггер
LOCK TABLE scores IN SHARE ROW EXCLUSIVE MODE;

INSERT INTO ledger (chat_id, user_id, delta, balance_after, kind)
SELECT chat_id, user_id, value, value, 'opening' FROM scores WHERE value <> 0;

CREATE TRIGGER scores_ledger
AFTER INSERT OR UPDATE OF value OR DELETE ON scores
FOR EACH ROW EXECUTE FUNCTION ledger_record_score_change();
