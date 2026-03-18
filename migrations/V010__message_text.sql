-- Добавляем хранение текста сообщения для /analyze и /wir
ALTER TABLE messages ADD COLUMN IF NOT EXISTS text TEXT;

-- Индекс для эффективной выборки последних N сообщений чата
CREATE INDEX IF NOT EXISTS idx_messages_chat_sent ON messages (chat_id, sent_at DESC);

-- Индекс для выборки по конкретным пользователям
CREATE INDEX IF NOT EXISTS idx_messages_chat_user_sent ON messages (chat_id, user_id, sent_at DESC);
