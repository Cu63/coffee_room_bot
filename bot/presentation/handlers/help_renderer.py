"""Рендерер интерактивной справки /help.

Читает структуру и тексты из configs/help.yaml (через load_help_config()),
динамические значения — из AppConfig.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter


def _cb(section: str, uid: int) -> str:
    return f"help:{section}:{uid}"


class HelpRenderer:
    """Строит тексты и клавиатуры для /help из YAML-конфига."""

    def __init__(self, help_cfg: dict) -> None:
        self._cfg = help_cfg

    # ── Клавиатуры ──────────────────────────────────────────────────

    def main_kb(self, uid: int) -> InlineKeyboardMarkup:
        rows = []
        for btn_row in self._cfg.get("menu_buttons", []):
            row = [
                InlineKeyboardButton(
                    text=btn["label"],
                    callback_data=_cb(btn["key"], uid),
                )
                for btn in btn_row
            ]
            rows.append(row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def back_kb(self, uid: int) -> InlineKeyboardMarkup:
        label = self._cfg.get("back_button", "⬅️ Назад")
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=_cb("main", uid))]]
        )

    # ── Тексты ──────────────────────────────────────────────────────

    def main_text(self, icon: str) -> str:
        return self._cfg.get("menu_title", "Справка").format(icon=icon)

    def section_text(
        self,
        section: str,
        config: AppConfig,
        formatter: MessageFormatter,
    ) -> str:
        s = self._cfg.get("sections", {}).get(section)
        if s is None:
            return ""
        p = formatter._p
        mc = config.mute
        tc = config.tag
        bjc = config.blackjack
        sc = config.slots
        dc = config.dice
        rc = config.renew
        lc = config.limits
        wgc = config.wordgame
        rwgc = config.rwordgame

        # Плейсхолдеры для секций с динамическими значениями
        ctx = dict(
            daily_reactions_given=lc.daily_negative_given,
            daily_score_received=lc.daily_score_received,
            max_message_age_hours=lc.max_message_age_hours,
            retention_days=config.history.retention_days,
            cost_per_minute=f"{mc.cost_per_minute} {p.pluralize(mc.cost_per_minute)}",
            min_minutes=mc.min_minutes,
            max_minutes=mc.max_minutes,
            selfmute_min=mc.selfmute_min_minutes,
            selfmute_max=mc.selfmute_max_minutes,
            protection_hours=mc.protection_duration_hours,
            protection_cost=mc.protection_cost,
            unmute_multiplier=mc.unmute_multiplier,
            cost_self=f"{tc.cost_self} {p.pluralize(tc.cost_self)}",
            cost_member=f"{tc.cost_member} {p.pluralize(tc.cost_member)}",
            cost_admin=f"{tc.cost_admin} {p.pluralize(tc.cost_admin)}",
            cost_owner=f"{tc.cost_owner} {p.pluralize(tc.cost_owner)}",
            max_length=tc.max_length,
            min_bet=f"{bjc.min_bet} {p.pluralize(bjc.min_bet)}",
            max_bet=f"{bjc.max_bet} {p.pluralize(bjc.max_bet)}",
            bj_min=bjc.min_bet,
            bj_max=bjc.max_bet,
            bj_max_games=bjc.max_games_per_window,
            bj_window=bjc.window_hours,
            mute_min=mc.min_minutes,
            mute_max=mc.max_minutes,
            slots_min=sc.min_bet,
            slots_max=sc.max_bet,
            slots_cooldown=sc.cooldown_minutes,
            dice_min=dc.min_bet,
            dice_max=dc.max_bet,
            renew_cost=f"{rc.cost} {p.pluralize(rc.cost)}",
            renew_limit=rc.daily_limit,
            wg_min_bet=wgc.min_bet,
            wg_max_bet=wgc.max_bet,
            wg_min_dur=wgc.min_duration_seconds // 60,
            wg_max_dur=wgc.max_duration_seconds // 60,
            wg_max_games=wgc.max_games_per_window,
            wg_window=wgc.game_window_hours,
            attempt_cost=wgc.attempt_cost,
            rwg_max_bet=rwgc.max_bet,
            rwg_min_len=rwgc.min_word_length,
            rwg_max_len=rwgc.max_word_length,
        )

        def _fmt(tmpl: str) -> str:
            try:
                return tmpl.format(**ctx)
            except KeyError:
                return tmpl

        if section == "reactions":
            lines_yaml = []
            for emoji, weight in config.reactions.items():
                sign = f"+{weight}" if weight > 0 else str(weight)
                lines_yaml.append(f"  {emoji} → {sign} {p.pluralize(abs(weight))}")
            return (
                s["header"]
                + "\n\n"
                + s["intro"].strip()
                + "\n\n"
                + "\n".join(lines_yaml)
                + "\n\n"
                + s["footer"].strip()
            )

        if section == "limits":
            rows = [_fmt(r) for r in s.get("rows", [])]
            return s["header"] + "\n\n" + "\n".join(rows)

        if section == "mute":
            parts = [
                s["header"],
                "",
                _fmt(s["cost_row"]),
                _fmt(s["range_row"]),
                s["no_debt"],
                "",
                s["selfmute_header"],
                _fmt(s["selfmute_row"]),
                "",
                s["unmute_header"],
            ]
            parts += [_fmt(r) for r in s.get("unmute_rows", [])]
            parts += [
                "",
                s["protect_header"],
            ]
            parts += [_fmt(r) for r in s.get("protect_rows", [])]
            parts += ["", s["time_formats"], "", s["admin_note"].strip()]
            return "\n".join(parts)

        if section == "tag":
            rows = [_fmt(r) for r in s.get("rows", [])]
            return s["header"] + "\n\n" + "\n".join(rows) + "\n\n" + s["clear_note"]

        if section == "bj":
            lines = [
                s["header"],
                "",
                _fmt(s["bet_row"]),
            ]
            lines += s.get("rules", [])
            lines += [""]
            lines += s.get("commands", [])
            return "\n".join(lines)

        if section == "wordgame":
            rows = [_fmt(r) for r in s.get("rows", [])]
            return s["header"] + "\n\n" + "\n".join(rows)

        if section == "commands":
            cmds = [_fmt(c) for c in s.get("static_commands", [])]
            return s["header"] + "\n\n" + "\n".join(cmds)

        if section == "admin":
            cmds = s.get("commands", [])
            return s["header"] + "\n\n" + "\n".join(cmds)

        return ""