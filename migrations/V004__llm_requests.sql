CREATE TABLE llm_requests (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL,
    chat_id     BIGINT      NOT NULL,
    command     VARCHAR(20) NOT NULL,   -- 'llm' или 'search'
    query       TEXT        NOT NULL,
    input_tokens  INT       NOT NULL DEFAULT 0,
    output_tokens INT       NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_requests_user_date ON llm_requests (user_id, created_at);
