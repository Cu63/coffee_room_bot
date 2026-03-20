"""PresentationProvider — MessageFormatter, HelpRenderer, конфиг, плюрализатор."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from bot.domain.pluralizer import ScorePluralizer
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.config_loader import (
    AppConfig,
    BotSettings,
    DatabaseConfig,
    load_config,
    load_help_config,
    load_messages,
)
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.handlers.help_renderer import HelpRenderer


class PresentationProvider(Provider):
    """Конфигурация, форматирование, рендеринг — синглтоны."""

    @provide(scope=Scope.APP)
    def get_bot_settings(self) -> BotSettings:
        return BotSettings()

    @provide(scope=Scope.APP)
    def get_db_config(self) -> DatabaseConfig:
        return DatabaseConfig()

    @provide(scope=Scope.APP)
    def get_config(self) -> AppConfig:
        return load_config()

    @provide(scope=Scope.APP)
    def get_score_pluralizer(self, config: AppConfig) -> ScorePluralizer:
        return ScorePluralizer(
            singular=config.score.singular,
            plural_few=config.score.plural_few,
            plural_many=config.score.plural_many,
            icon=config.score.icon,
        )

    @provide(scope=Scope.APP)
    def get_messages(self, config: AppConfig, pluralizer: ScorePluralizer) -> MessageFormatter:
        return MessageFormatter(load_messages(), pluralizer)

    @provide(scope=Scope.APP)
    def get_reaction_registry(self, config: AppConfig) -> ReactionRegistry:
        return ReactionRegistry(config.reactions)

    @provide(scope=Scope.APP)
    def get_help_renderer(self) -> HelpRenderer:
        return HelpRenderer(load_help_config())
