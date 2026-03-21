"""AppServiceProvider — все сервисы."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from bot.application.analyze_service import AnalyzeService
from bot.application.chatmode_service import ChatmodeService
from bot.application.cleanup_service import CleanupService
from bot.application.daily_leaderboard_service import DailyLeaderboardService
from bot.application.dice_service import DiceService
from bot.application.giveaway_service import GiveawayService
from bot.application.history_service import HistoryService
from bot.application.interfaces.chatmode_repository import IChatmodeRepository
from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
from bot.application.interfaces.daily_limits_repository import IDailyLimitsRepository
from bot.application.interfaces.dice_repository import IDiceRepository
from bot.application.interfaces.event_repository import IEventRepository
from bot.application.interfaces.giveaway_repository import IGiveawayRepository
from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.message_repository import IMessageRepository
from bot.application.interfaces.mute_repository import IMuteRepository
from bot.application.interfaces.per_target_limits_repository import IPerTargetLimitsRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.interfaces.xp_repository import IXpRepository
from bot.application.leaderboard_service import LeaderboardService
from bot.application.llm_service import LlmService
from bot.application.mute_service import MuteService
from bot.application.score_service import ScoreService
from bot.application.xp_service import XpService
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient
from bot.infrastructure.search_engine import SearchEngine


class AppServiceProvider(Provider):

    @provide(scope=Scope.REQUEST)
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

    @provide(scope=Scope.REQUEST)
    def get_leaderboard_service(self, score_repo: IScoreRepository) -> LeaderboardService:
        return LeaderboardService(score_repo)

    @provide(scope=Scope.REQUEST)
    def get_daily_leaderboard_service(
        self, repo: IDailyLeaderboardRepository, score_service: ScoreService, pluralizer: ScorePluralizer,
    ) -> DailyLeaderboardService:
        return DailyLeaderboardService(repo, score_service, pluralizer)

    @provide(scope=Scope.REQUEST)
    def get_chatmode_service(self, repo: IChatmodeRepository, score_service: ScoreService) -> ChatmodeService:
        return ChatmodeService(repo, score_service)

    @provide(scope=Scope.REQUEST)
    def get_history_service(self, event_repo: IEventRepository, config: AppConfig) -> HistoryService:
        return HistoryService(event_repo, config.history.retention_days)

    @provide(scope=Scope.REQUEST)
    def get_cleanup_service(self, event_repo: IEventRepository, config: AppConfig) -> CleanupService:
        return CleanupService(event_repo, config.history.retention_days)

    @provide(scope=Scope.REQUEST)
    def get_mute_service(self, mute_repo: IMuteRepository) -> MuteService:
        return MuteService(mute_repo)

    @provide(scope=Scope.REQUEST)
    def get_dice_service(
        self, dice_repo: IDiceRepository, score_repo: IScoreRepository, stats_repo: IUserStatsRepository,
    ) -> DiceService:
        return DiceService(dice_repo, score_repo, stats_repo)

    @provide(scope=Scope.REQUEST)
    def get_giveaway_service(
        self, repo: IGiveawayRepository, score_repo: IScoreRepository, stats_repo: IUserStatsRepository,
    ) -> GiveawayService:
        return GiveawayService(repo, score_repo, stats_repo)

    @provide(scope=Scope.REQUEST)
    def get_analyze_service(
        self, client: OpenAiClient, message_repo: IMessageRepository,
        llm_repo: ILlmRepository, config: AppConfig, formatter: MessageFormatter,
    ) -> AnalyzeService:
        return AnalyzeService(
            client=client, message_repo=message_repo, llm_repo=llm_repo,
            config=config.analyze, formatter=formatter, admin_users=config.admin.users,
        )

    @provide(scope=Scope.REQUEST)
    def get_llm_service(
        self, llm_repo: ILlmRepository, client: AiTunnelClient,
        config: AppConfig, settings: BotSettings,
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

    @provide(scope=Scope.REQUEST)
    def get_xp_service(
        self, xp_repo: IXpRepository, config: AppConfig,
    ) -> XpService:
        return XpService(xp_repo=xp_repo, config=config.xp)
