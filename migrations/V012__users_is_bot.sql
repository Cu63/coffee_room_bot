-- Помечаем ботов в таблице пользователей, чтобы исключать их из лидербордов
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot BOOLEAN NOT NULL DEFAULT FALSE;

-- Проставляем индекс — фильтр WHERE NOT is_bot используется часто
CREATE INDEX IF NOT EXISTS idx_users_is_bot ON users (is_bot) WHERE is_bot = FALSE;
