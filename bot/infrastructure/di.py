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
from bot.application.interfaces.mute_protection_repository import IMuteProtectionRepository
from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository
from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.application.interfaces.giveaway_repository import IGiveawayRepository
from bot.application.score_service import ScoreService
from bot.application.leaderboard_service import LeaderboardService
from bot.application.history_service import HistoryService
from bot.application.cleanup_service import CleanupService
from bot.application.mute_service import MuteService
from bot.application.llm_service import LlmService
from bot.application.giveaway_service import GiveawayService

from bot.infrastructure.config_loader import AppConfig, Settings, load_config, load_messages
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.search_engine import SearchEngine
from bot.infrastructure.db.transaction_manager import PostgresTransactionManager
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_event_repository import PostgresEventRepository
from bot.infrastructure.db.postgres_daily_limits_repository import PostgresDailyLimitsRepository
from bot.infrastructure.db.postgres_user_repository import PostgresUserRepository
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from bot.infrastructure.db.postgres_mute_repository import PostgresMuteRepository
from bot.infrastructure.db.postgres_mute_protection_repository import PostgresMuteProtectionRepository
from bot.infrastructure.db.postgres_saved_permissions_repository import PostgresSavedPermissionsRepository
from bot.infrastructure.db.postgres_llm_repository import PostgresLlmRepository
from bot.infrastructure.db.postgres_giveaway_repository import PostgresGiveawayRepository
from bot.application.slots_service import SlotsConfig, SlotsMachine, SlotsService
from bot.application.slots_custom_functions import apply_custom_functions


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
    def get_pluralizer(self, config: AppConfig) -> ScorePluralizer:
        return ScorePluralizer(
            singular=config.score.singular,
            plural_few=config.score.plural_few,
            plural_many=config.score.plural_many,
            icon=config.score.icon,
        )

    @provide
    def get_reaction_registry(self, config: AppConfig) -> ReactionRegistry:
        return ReactionRegistry(config.reactions)

    @provide
    def get_search_engine(self, settings: Settings) -> SearchEngine:
        return SearchEngine(base_url=settings.openserp_url)

    @provide
    async def get_aitunnel_client(
        self, settings: Settings, config: AppConfig,
    ) -> AsyncIterable[AiTunnelClient]:
        client = AiTunnelClient(
            api_key=settings.aitunnel_api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            max_output_tokens=config.llm.max_output_tokens,
        )
        yield client
        await client.close()

    @provide
    async def get_pool(self, settings: Settings) -> AsyncIterable[asyncpg.Pool]:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        yield pool
        await pool.close()

    @provide
    def get_slots_config(self, config: AppConfig) -> SlotsConfig:
        cfg = SlotsConfig(
            min_bet=config.blackjack.min_bet,
            max_bet=config.blackjack.max_bet,
        )
        apply_custom_functions(cfg)
        return cfg

    @provide
    def get_slots_machine(self, cfg: SlotsConfig) -> SlotsMachine:
        return SlotsMachine(cfg)


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
    def get_mute_protection_repo(self, tm: ITransactionManager) -> IMuteProtectionRepository:
        return PostgresMuteProtectionRepository(tm.get_connection())

    @provide
    def get_saved_perms_repo(self, tm: ITransactionManager) -> ISavedPermissionsRepository:
        return PostgresSavedPermissionsRepository(tm.get_connection())

    @provide
    def get_giveaway_repo(self, tm: ITransactionManager) -> IGiveawayRepository:
        return PostgresGiveawayRepository(tm.get_connection())

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

    @provide
    def get_slots_service(self, machine: SlotsMachine, cfg: SlotsConfig, score_service: ScoreService) -> SlotsService:
        return SlotsService(machine, cfg, score_service)

    @provide
    def get_llm_repo(self, tm: ITransactionManager) -> ILlmRepository:
        return PostgresLlmRepository(tm.get_connection())

    @provide
    def get_llm_service(
        self,
        client: AiTunnelClient,
        search_engine: SearchEngine,
        llm_repo: ILlmRepository,
        config: AppConfig,
    ) -> LlmService:
        return LlmService(
            client=client,
            search_engine=search_engine,
            llm_repo=llm_repo,
            system_prompt=config.llm.system_prompt,
            search_system_prompt=config.llm.search_system_prompt,
            daily_limit=config.llm.daily_limit_per_user,
            search_max_results=config.llm.search_max_results,
            admin_users=config.admin.users,
        )

    @provide
    def get_giveaway_service(
        self,
        giveaway_repo: IGiveawayRepository,
        score_repo: IScoreRepository,
    ) -> GiveawayService:
        return GiveawayService(
            giveaway_repo=giveaway_repo,
            score_repo=score_repo,
        )