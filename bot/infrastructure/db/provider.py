"""DatabaseProvider — пул, транзакции, все postgres-репозитории."""

from __future__ import annotations

from collections.abc import AsyncIterable

import asyncpg
from dishka import Provider, Scope, provide

from bot.application.interfaces.chatmode_repository import IChatmodeRepository
from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
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
from bot.infrastructure.config_loader import DatabaseConfig
from bot.infrastructure.db.postgres_chatmode_repository import PostgresChatmodeRepository
from bot.infrastructure.db.postgres_daily_leaderboard_repository import PostgresDailyLeaderboardRepository
from bot.infrastructure.db.postgres_daily_limits_repository import PostgresDailyLimitsRepository
from bot.infrastructure.db.postgres_dice_repository import PostgresDiceRepository
from bot.infrastructure.db.postgres_event_repository import PostgresEventRepository
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


class DatabaseProvider(Provider):
    """Пул соединений (APP) + транзакции и репозитории (REQUEST)."""

    @provide(scope=Scope.APP)
    async def get_pool(self, db: DatabaseConfig) -> AsyncIterable[asyncpg.Pool]:
        dsn = db.dsn.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        yield pool
        await pool.close()

    @provide(scope=Scope.REQUEST)
    async def get_tx_manager(self, pool: asyncpg.Pool) -> AsyncIterable[ITransactionManager]:
        tm = PostgresTransactionManager(pool)
        await tm.begin()
        try:
            yield tm
            await tm.commit()
        except Exception:
            await tm.rollback()
            raise

    @provide(scope=Scope.REQUEST)
    def get_score_repo(self, tm: ITransactionManager) -> IScoreRepository:
        return PostgresScoreRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_event_repo(self, tm: ITransactionManager) -> IEventRepository:
        return PostgresEventRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_daily_limits_repo(self, tm: ITransactionManager) -> IDailyLimitsRepository:
        return PostgresDailyLimitsRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_user_repo(self, tm: ITransactionManager) -> IUserRepository:
        return PostgresUserRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_message_repo(self, tm: ITransactionManager) -> IMessageRepository:
        return PostgresMessageRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_mute_repo(self, tm: ITransactionManager) -> IMuteRepository:
        return PostgresMuteRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_saved_perms_repo(self, tm: ITransactionManager) -> ISavedPermissionsRepository:
        return PostgresSavedPermissionsRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_mute_protection_repo(self, tm: ITransactionManager) -> IMuteProtectionRepository:
        return PostgresMuteProtectionRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_user_stats_repo(self, tm: ITransactionManager) -> IUserStatsRepository:
        return PostgresUserStatsRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_daily_leaderboard_repo(self, tm: ITransactionManager) -> IDailyLeaderboardRepository:
        return PostgresDailyLeaderboardRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_chatmode_repo(self, tm: ITransactionManager) -> IChatmodeRepository:
        return PostgresChatmodeRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_per_target_limits_repo(self, tm: ITransactionManager) -> IPerTargetLimitsRepository:
        return PostgresPerTargetLimitsRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_dice_repo(self, tm: ITransactionManager) -> IDiceRepository:
        return PostgresDiceRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_giveaway_repo(self, tm: ITransactionManager) -> IGiveawayRepository:
        return PostgresGiveawayRepository(tm.get_connection())

    @provide(scope=Scope.REQUEST)
    def get_llm_repo(self, tm: ITransactionManager) -> ILlmRepository:
        return PostgresLlmRepository(tm.get_connection())
