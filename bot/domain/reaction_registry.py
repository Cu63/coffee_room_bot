from bot.domain.entities import Reaction


class ReactionRegistry:
    """Реестр допустимых реакций и их весов."""

    def __init__(self, reactions: dict[str, int]) -> None:
        self._reactions = {
            emoji: Reaction(emoji=emoji, weight=weight)
            for emoji, weight in reactions.items()
        }

    def get(self, emoji: str) -> Reaction | None:
        return self._reactions.get(emoji)
