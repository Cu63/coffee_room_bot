from collections.abc import AsyncIterable

import asyncpg
import redis.asyncio as aioredis
from dishka import Provider, Scope, provide

from bot.application.cleanup_service import CleanupService
from bot.application.analyze_service import AnalyzeService
from bot.application.dice_service import DiceService
from bot.application.giveaway_service import GiveawayService
from bot.application.history_service import HistoryService
from bot.application.interfaces.daily_limits_repository import IDailyLimitsRepository
from bot.application.interfaces.dice_repository import IDiceRepository
from bot.application.interfaces.event_repository import IEventRepository
from bot.application.interfaces.giveaway_repository import IGiveawayRepository
from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.message_repository import IMessageRepository
from bot.application.interfaces.mute_protection_repository import IMuteProtectionRepository
from bot.application.interfaces.mute_repository import IMuteRepository
from bot.application.interfaces.per_target_limits_repository import IPerTargetLimitsRepository
from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.leaderboard_service import LeaderboardService
from bot.application.llm_service import LlmService
from bot.application.mute_service import MuteService
from bot.application.score_service import ScoreService
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.openai_client import OpenAiClient
from bot.infrastructure.config_loader import AppConfig, BotSettings, DatabaseConfig, load_config, load_help_config, load_messages
from bot.infrastructure.db.postgres_daily_limits_repository import PostgresDailyLimitsRepository
from bot.infrastructure.db.postgres_event_repository import PostgresEventRepository
from bot.infrastructure.db.postgres_dice_repository import PostgresDiceRepository
from bot.infrastructure.db.postgres_giveaway_repository import PostgresGiveawayRepository
from bot.infrastructure.db.postgres_llm_repository import PostgresLlmRepository
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from bot.infrastructure.db.postgres_mute_protection_repository import PostgresMuteProtectionRepository
from bot.infrastructure.db.postgres_mute_repository import PostgresMuteRepository
from bot.infrastructure.db.postgres_per_target_limits_repository import PostgresPerTargetLimitsRepository
from bot.infrastructure.db.postgres_saved_permissions_repository import PostgresSavedPermissionsRepository
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_user_repository import PostgresUserRepository
from bot.infrastructure.db.postgres_user_stats_repository import PostgresUserStatsRepository
from bot.infrastructure.db.transaction_manager import PostgresTransactionManager
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.infrastructure.search_engine import SearchEngine
from bot.presentation.handlers.help_renderer import HelpRenderer


class AppProvider(Provider):
    """Синглтоны на всё время жизни приложения."""

    scope = Scope.APP

    @provide
    def get_bot_settings(self) -> BotSettings:
        return BotSettings()

    @provide
    def get_db_config(self) -> DatabaseConfig:
        return DatabaseConfig()

    @provide
    def get_config(self) -> AppConfig:
        return load_config()

    @provide
    def get_score_pluralizer(self, config: AppConfig) -> ScorePluralizer:
        return ScorePluralizer(
            singular=config.score.singular,
            plural_few=config.score.plural_few,
            plural_many=config.score.plural_many,
            icon=config.score.icon,
        )

    @provide
    def get_messages(self, config: AppConfig, pluralizer: ScorePluralizer) -> MessageFormatter:
        templates = load_messages()
        return MessageFormatter(templates, pluralizer)

    @provide
    def get_reaction_registry(self, config: AppConfig) -> ReactionRegistry:
        return ReactionRegistry(config.reactions)

    @provide
    def get_help_renderer(self) -> HelpRenderer:
        return HelpRenderer(load_help_config())

    @provide
    async def get_pool(self, db: DatabaseConfig) -> AsyncIterable[asyncpg.Pool]:
        dsn = db.dsn.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        yield pool
        await pool.close()

    @provide
    async def get_redis(self, settings: BotSettings) -> AsyncIterable[aioredis.Redis]:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        yield r
        await r.aclose()

    @provide
    def get_redis_store(self, redis: aioredis.Redis) -> RedisStore:
        return RedisStore(redis)



class RequestProvider(Provider):
    """Создаются на каждый запрос (хэндлер)."""

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
    def get_mute_protection_repo(self, tm: ITransactionManager) -> IMuteProtectionRepository:
        return PostgresMuteProtectionRepository(tm.get_connection())

    @provide
    def get_user_stats_repo(self, tm: ITransactionManager) -> IUserStatsRepository:
        return PostgresUserStatsRepository(tm.get_connection())

    @provide
    def get_per_target_limits_repo(self, tm: ITransactionManager) -> IPerTargetLimitsRepository:
        return PostgresPerTargetLimitsRepository(tm.get_connection())

    @provide
    def get_dice_repo(self, tm: ITransactionManager) -> IDiceRepository:
        return PostgresDiceRepository(tm.get_connection())

    @provide
    def get_giveaway_repo(self, tm: ITransactionManager) -> IGiveawayRepository:
        return PostgresGiveawayRepository(tm.get_connection())

    @provide
    def get_llm_repo(self, tm: ITransactionManager) -> ILlmRepository:
        return PostgresLlmRepository(tm.get_connection())

    @provide
    def get_score_service(
        self,
        score_repo: IScoreRepository,
        event_repo: IEventRepository,
        limits_repo: IDailyLimitsRepository,
        message_repo: IMessageRepository,
        registry: ReactionRegistry,
        per_target_repo: IPerTargetLimitsRepository,
        stats_repo: IUserStatsRepository,
        config: AppConfig,
    ) -> ScoreService:
        return ScoreService(
            score_repo=score_repo,
            event_repo=event_repo,
            limits_repo=limits_repo,
            message_repo=message_repo,
            reaction_registry=registry,
            per_target_limits_repo=per_target_repo,
            stats_repo=stats_repo,
            self_reaction_allowed=config.self_reaction_allowed,
            daily_negative_given=config.limits.daily_negative_given,
            daily_positive_per_target=config.limits.daily_positive_per_target,
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

    @provide
    def get_dice_service(
        self,
        dice_repo: IDiceRepository,
        score_repo: IScoreRepository,
        stats_repo: IUserStatsRepository,
    ) -> DiceService:
        return DiceService(dice_repo, score_repo, stats_repo)

    @provide
    def get_giveaway_service(
        self,
        repo: IGiveawayRepository,
        score_repo: IScoreRepository,
        stats_repo: IUserStatsRepository,
    ) -> GiveawayService:
        return GiveawayService(repo, score_repo, stats_repo)

    @provide
    def get_aitunnel_client(self, settings: BotSettings, config: AppConfig) -> AiTunnelClient:
        return AiTunnelClient(
            api_key=settings.aitunnel_api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            max_output_tokens=config.llm.max_output_tokens,
        )

    @provide
    def get_openai_client(self, settings: BotSettings, config: AppConfig) -> OpenAiClient:
        return OpenAiClient(
            api_key=settings.openai_api_key,
            base_url=config.analyze.base_url,
            model=config.analyze.model,
            max_output_tokens=config.analyze.max_output_tokens,
        )

    @provide
    def get_analyze_service(
        self,
        client: OpenAiClient,
        message_repo: IMessageRepository,
        config: AppConfig,
    ) -> AnalyzeService:
        return AnalyzeService(
            client=client,
            message_repo=message_repo,
            config=config.analyze,
        )

    @provide
    def get_llm_service(
        self,
        llm_repo: ILlmRepository,
        client: AiTunnelClient,
        config: AppConfig,
        settings: BotSettings,
    ) -> LlmService:
        return LlmService(
            client=client,
            search_engine=SearchEngine(settings.openserp_url),
            llm_repo=llm_repo,
            system_prompt=config.llm.system_prompt,
            search_system_prompt=config.llm.search_system_prompt,
            daily_limit=config.llm.daily_limit_per_user,
            search_max_results=config.llm.search_max_results,
            admin_users=config.admin.users,
        )