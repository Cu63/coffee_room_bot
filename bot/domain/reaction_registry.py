from bot.domain.entities import Reaction
from bot.domain.emoji_utils import normalize_emoji


class ReactionRegistry:
    """Реестр допустимых реакций и их весов."""

    def __init__(self, reactions: dict[str, int]) -> None:
        self._reactions = {
            normalize_emoji(emoji): Reaction(emoji=normalize_emoji(emoji), weight=weight)
            for emoji, weight in reactions.items()
        }

    def get(self, emoji: str) -> Reaction | None:
        return self._reactions.get(normalize_emoji(emoji))
