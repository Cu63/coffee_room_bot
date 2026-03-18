from bot.infrastructure.aitunnel_client import AiTunnelClient


class OpenAiClient(AiTunnelClient):
    """OpenAI-совместимый клиент для команд /analyze и /wir.

    Использует отдельный API-ключ (OPENAI_API_KEY) и endpoint proxyapi.ru.
    Наследует всю логику у AiTunnelClient — тот уже реализует OpenAI Chat
    Completions API. Отдельный класс нужен для однозначной идентификации
    в DI-контейнере (dishka использует тип как ключ).
    """
