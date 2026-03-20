"""LlmProvider — AiTunnelClient, OpenAiClient."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.openai_client import OpenAiClient


class LlmProvider(Provider):

    @provide(scope=Scope.REQUEST)
    def get_aitunnel_client(self, settings: BotSettings, config: AppConfig) -> AiTunnelClient:
        return AiTunnelClient(
            api_key=settings.aitunnel_api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            max_output_tokens=config.llm.max_output_tokens,
        )

    @provide(scope=Scope.REQUEST)
    def get_openai_client(self, settings: BotSettings, config: AppConfig) -> OpenAiClient:
        return OpenAiClient(
            api_key=settings.openai_api_key,
            base_url=config.analyze.base_url,
            model=config.analyze.model,
            max_output_tokens=config.analyze.max_output_tokens,
        )
