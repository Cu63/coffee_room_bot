class ScorePluralizer:
    """Склонение названия очков по числу (русские правила)."""

    def __init__(self, singular: str, plural_few: str, plural_many: str, icon: str = "") -> None:
        self._singular = singular
        self._plural_few = plural_few
        self._plural_many = plural_many
        self.icon = icon

    def pluralize(self, n: int) -> str:
        n = abs(n)
        mod10 = n % 10
        mod100 = n % 100

        if 11 <= mod100 <= 19:
            return self._plural_many
        if mod10 == 1:
            return self._singular
        if 2 <= mod10 <= 4:
            return self._plural_few
        return self._plural_many
