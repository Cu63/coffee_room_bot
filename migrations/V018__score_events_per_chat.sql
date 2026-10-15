-- score_events — только реакции на сообщения; история денег теперь в ledger (V017).
--
-- 1. Реакция уникальна в пределах чата: номера сообщений в разных чатах повторяются,
--    а UNIQUE (actor_id, message_id, emoji) считал реакцию на сообщение с тем же
--    номером в другом чате дублем. Старое ограничение ищем по определению, а не
--    по имени: имя генерировал Postgres.
DO $$
DECLARE
    c TEXT;
BEGIN
    SELECT conname INTO c
    FROM pg_constraint
    WHERE conrelid = 'score_events'::regclass
      AND contype = 'u'
      AND pg_get_constraintdef(oid) = 'UNIQUE (actor_id, message_id, emoji)';
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE score_events DROP CONSTRAINT %I', c);
    END IF;
END $$;

ALTER TABLE score_events
    ADD CONSTRAINT score_events_chat_actor_message_emoji_key UNIQUE (chat_id, actor_id, message_id, emoji);

-- 2. Служебные псевдо-события (отрицательный message_id) больше не пишутся: переводы,
--    покупки и бонусы видны в истории из ledger. Старые убираем — они попадали в рейтинг
--    дня как реакции.
DELETE FROM score_events WHERE message_id < 0;
