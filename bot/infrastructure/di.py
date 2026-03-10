from collections.abc import AsyncIterable

import asyncpg
from dishka import Provider, Scope, provide

from bot.domain.pluralizer import ScorePluralizer
from bot.domain.reaction_registry import ReactionRegistry

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.event_repository import IEventRepository
from bot.application.interfaces.daily_limits_repository import IDailyLimitsRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.message_repository import IMessageRepository
from bot.application.interfaces.mute_repository import IMuteRepository
from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository
from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.application.score_service import ScoreService
from bot.application.leaderboard_service import LeaderboardService
from bot.application.history_service import HistoryService
from bot.application.cleanup_service import CleanupService
from bot.application.mute_service import MuteService

from bot.infrastructure.config_loader import AppConfig, Settings, load_config, load_messages
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.db.transaction_manager import PostgresTransactionManager
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_event_repository import PostgresEventRepository
from bot.infrastructure.db.postgres_daily_limits_repository import PostgresDailyLimitsRepository
from bot.infrastructure.db.postgres_user_repository import PostgresUserRepository
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from bot.infrastructure.db.postgres_mute_repository import PostgresMuteRepository
from bot.infrastructure.db.postgres_saved_permissions_repository import PostgresSavedPermissionsRepository


class AppProvider(Provider):
    """Синглтоны на всё время жизни приложения."""

    scope = Scope.APP

    @provide
    def get_settings(self) -> Settings:
        return Settings()

    @provide
    def get_config(self) -> AppConfig:
        return load_config()

    @provide
    def get_messages(self, config: AppConfig) -> MessageFormatter:
        templates = load_messages()
        pluralizer = ScorePluralizer(
            singular=config.score.singular,
            plural_few=config.score.plural_few,
            plural_many=config.score.plural_many,
            icon=config.score.icon,
        )
        return MessageFormatter(templates, pluralizer)

    @provide
    def get_reaction_registry(self, config: AppConfig) -> ReactionRegistry:
        return ReactionRegistry(config.reactions)

    @provide
    async def get_pool(self, settings: Settings) -> AsyncIterable[asyncpg.Pool]:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        yield pool
        await pool.close()


class RequestProvider(Provider):
    """Создаются на каждый запрос (хэндлер). Транзакция управляется здесь."""

    scope = Scope.REQUEST

    @provide
    async def get_tx_manager(self, pool: asyncpg.Pool) -> AsyncIterable[ITransactionManager]:
        tm = PostgresTransactionManager(pool)
        await tm.begin()
        try:
            yield tm
            await tm.commit()
        except Exception:
            await tm.rollback()
            raise

    @provide
    def get_score_repo(self, tm: ITransactionManager) -> IScoreRepository:
        return PostgresScoreRepository(tm.get_connection())

    @provide
    def get_event_repo(self, tm: ITransactionManager) -> IEventRepository:
        return PostgresEventRepository(tm.get_connection())

    @provide
    def get_daily_limits_repo(self, tm: ITransactionManager) -> IDailyLimitsRepository:
        return PostgresDailyLimitsRepository(tm.get_connection())

    @provide
    def get_user_repo(self, tm: ITransactionManager) -> IUserRepository:
        return PostgresUserRepository(tm.get_connection())

    @provide
    def get_message_repo(self, tm: ITransactionManager) -> IMessageRepository:
        return PostgresMessageRepository(tm.get_connection())

    @provide
    def get_mute_repo(self, tm: ITransactionManager) -> IMuteRepository:
        return PostgresMuteRepository(tm.get_connection())

    @provide
    def get_saved_perms_repo(self, tm: ITransactionManager) -> ISavedPermissionsRepository:
        return PostgresSavedPermissionsRepository(tm.get_connection())

    @provide
    def get_score_service(
        self,
        score_repo: IScoreRepository,
        event_repo: IEventRepository,
        limits_repo: IDailyLimitsRepository,
        message_repo: IMessageRepository,
        registry: ReactionRegistry,
        config: AppConfig,
    ) -> ScoreService:
        return ScoreService(
            score_repo=score_repo,
            event_repo=event_repo,
            limits_repo=limits_repo,
            message_repo=message_repo,
            reaction_registry=registry,
            self_reaction_allowed=config.self_reaction_allowed,
            daily_reactions_given=config.limits.daily_reactions_given,
            daily_score_received=config.limits.daily_score_received,
            max_message_age_hours=config.limits.max_message_age_hours,
        )

    @provide
    def get_leaderboard_service(self, score_repo: IScoreRepository) -> LeaderboardService:
        return LeaderboardService(score_repo)

    @provide
    def get_history_service(self, event_repo: IEventRepository, config: AppConfig) -> HistoryService:
        return HistoryService(event_repo, config.history.retention_days)

    @provide
    def get_cleanup_service(self, event_repo: IEventRepository, config: AppConfig) -> CleanupService:
        return CleanupService(event_repo, config.history.retention_days)

    @provide
    def get_mute_service(self, mute_repo: IMuteRepository) -> MuteService:
        return MuteService(mute_repo)