"""LlmProvider — AiTunnelClient, OpenAiClient, GigaChatClient."""

from __future__ import annotations

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide

from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.gigachat_client import GigaChatClient
from bot.infrastructure.openai_client import OpenAiClient


class LlmProvider(Provider):
    # Клиенты держат aiohttp-сессию: один клиент на процесс, сессия закрывается
    # при остановке. Раньше клиент создавался на каждый запрос, и сессии не закрывались.
    @provide(scope=Scope.APP)
    async def get_aitunnel_client(self, settings: BotSettings, config: AppConfig) -> AsyncIterable[AiTunnelClient]:
        client = AiTunnelClient(
            api_key=settings.aitunnel_api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            max_output_tokens=config.llm.max_output_tokens,
        )
        yield client
        await client.close()

    # GigaChat живёт в APP-скоупе: один access_token и одна HTTP-сессия
    # на весь процесс (токен действует 30 минут, переспрашивать его на
    # каждый запрос — лишний round-trip).
    @provide(scope=Scope.APP)
    async def get_gigachat_client(self, settings: BotSettings, config: AppConfig) -> AsyncIterable[GigaChatClient]:
        client = GigaChatClient(
            api_key=settings.gigachat_api_key,
            base_url=config.genai.base_url,
            oauth_url=config.genai.oauth_url,
            scope=config.genai.scope,
            model=config.genai.model,
            verify_ssl=config.genai.verify_ssl,
            timeout=config.genai.timeout,
        )
        yield client
        await client.close()

    @provide(scope=Scope.APP)
    async def get_openai_client(self, settings: BotSettings, config: AppConfig) -> AsyncIterable[OpenAiClient]:
        client = OpenAiClient(
            api_key=settings.openai_api_key,
            base_url=config.analyze.base_url,
            model=config.analyze.model,
            max_output_tokens=config.analyze.max_output_tokens,
        )
        yield client
        await client.close()
