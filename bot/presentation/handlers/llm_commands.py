from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.llm_service import LlmResult, LlmService, RateLimitExceeded
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.html import sanitize_html
from bot.presentation.middlewares.llm_busy import OneLlmRequestPerUserMiddleware
from bot.presentation.utils import send_html_parts

logger = logging.getLogger(__name__)
router = Router(name="llm_commands")
router.message.middleware(OneLlmRequestPerUserMiddleware())

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


_LINK_RE = re.compile(r"(<a\s[^>]*>)(.*?)(</a>)", re.DOTALL)
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_SUP_RE = re.compile(r"<sup[^>]*>.*?</sup>", re.DOTALL | re.IGNORECASE)
_SUB_RE = re.compile(r"<sub[^>]*>.*?</sub>", re.DOTALL | re.IGNORECASE)
_SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.DOTALL | re.IGNORECASE)
_DIV_RE = re.compile(r"</?div[^>]*>", re.IGNORECASE)
_MD_LIST_RE = re.compile(r"^(\s*)\*\s+", re.MULTILINE)
_FOOTNOTE_RE = re.compile(r"\s*\[\d+\]")
# Голый URL НЕ внутри href="..." и НЕ внутри >...</a>
_BARE_URL_RE = re.compile(r'(?<!href=["\'])(?<!["\'>])(https?://\S+)')
# Строка вида "  URL: https://..." — убираем целиком (артефакт LLM)
_URL_LINE_RE = re.compile(r"^\s*URL:\s*https?://\S+\s*$", re.MULTILINE)


def _shorten_bare_urls(text: str) -> str:
    """Убирает строки 'URL: ...' и оборачивает голые URL в <a href> с коротким доменом."""
    text = _URL_LINE_RE.sub("", text)

    def _replace_url(m: re.Match) -> str:
        url = m.group(1).rstrip(".,;:!?)")
        domain = urlparse(url).netloc.removeprefix("www.")
        return f'<a href="{url}">{domain}</a>'

    return _BARE_URL_RE.sub(_replace_url, text)


def _md_to_html(text: str) -> str:
    """Конвертирует базовый Markdown в Telegram HTML и убирает неподдерживаемые теги."""
    # Убираем неподдерживаемые Telegram теги
    text = _SPAN_RE.sub(r"\1", text)
    text = _DIV_RE.sub("", text)
    text = _SUP_RE.sub("", text)
    text = _SUB_RE.sub("", text)
    text = _FOOTNOTE_RE.sub("", text)
    # Конвертируем * списки → —
    text = _MD_LIST_RE.sub(r"\1— ", text)
    # Конвертируем MD → HTML
    text = _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _MD_BOLD_RE.sub(r"<b>\1</b>", text)
    text = _MD_ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


def _bold_underline_links(text: str) -> str:
    """Оборачивает все <a> теги в <b><u>...</u></b>."""
    return _LINK_RE.sub(r"<b><u>\1\2\3</u></b>", text)


def _usage_footer(result: LlmResult) -> str:
    if result.is_admin:
        return f"\n\n<i>Запросов сегодня: {result.used_today} (админ, без лимита)</i>"
    remaining = max(0, result.daily_limit - result.used_today)
    return f"\n\n<i>Осталось запросов на сегодня: {remaining}/{result.daily_limit}</i>"


async def _send_llm_response(msg: Message, result: LlmResult) -> None:
    """Ответ LLM: Markdown → HTML Telegram, чистка недоверенного HTML, длинный — частями.

    Чистим до подписи о лимите: незакрытый тег модели не должен её проглотить.
    """
    answer = sanitize_html(_bold_underline_links(_shorten_bare_urls(_md_to_html(result.text))))
    await send_html_parts(msg, msg, answer + _usage_footer(result))


@router.message(Command("llm"), F.chat.type != "private")
@inject
async def cmd_llm(
    message: Message,
    command: CommandObject,
    llm_service: FromDishka[LlmService],
    formatter: FromDishka[MessageFormatter],
    settings: FromDishka[BotSettings],
    config: FromDishka[AppConfig],
) -> None:
    """Прямой вопрос к LLM."""
    if not settings.aitunnel_api_key:
        await message.reply(formatter._t["llm_no_key"])
        return

    if not command.args:
        await message.reply(formatter._t["llm_usage"])
        return

    if message.from_user is None:
        return

    thinking = await message.reply(formatter._t["llm_thinking"])
    try:
        result = await llm_service.ask(
            message.from_user.id,
            message.chat.id,
            message.from_user.username,
            command.args,
        )
        await _send_llm_response(thinking, result)
    except RateLimitExceeded:
        await thinking.edit_text(
            formatter._t["llm_rate_limit"].format(limit=config.llm.daily_limit_per_user),
        )
    except Exception:
        logger.exception("LLM request failed")
        await thinking.edit_text(formatter._t["llm_error"])


@router.message(Command("search"), F.chat.type != "private")
@inject
async def cmd_search(
    message: Message,
    command: CommandObject,
    llm_service: FromDishka[LlmService],
    formatter: FromDishka[MessageFormatter],
    settings: FromDishka[BotSettings],
    config: FromDishka[AppConfig],
) -> None:
    """Поиск в интернете + ответ LLM по результатам."""
    if not settings.aitunnel_api_key:
        await message.reply(formatter._t["llm_no_key"])
        return

    if not command.args:
        await message.reply(formatter._t["search_usage"])
        return

    if message.from_user is None:
        return

    thinking = await message.reply(formatter._t["search_thinking"])
    try:
        result = await llm_service.search_and_answer(
            message.from_user.id,
            message.chat.id,
            message.from_user.username,
            command.args,
        )
        await _send_llm_response(thinking, result)
    except RateLimitExceeded:
        await thinking.edit_text(
            formatter._t["llm_rate_limit"].format(limit=config.llm.daily_limit_per_user),
        )
    except Exception:
        logger.exception("Search request failed")
        await thinking.edit_text(formatter._t["llm_error"])


@router.message(Command("searchd"), F.chat.type != "private")
@inject
async def cmd_search_debug(
    message: Message,
    command: CommandObject,
    llm_service: FromDishka[LlmService],
    formatter: FromDishka[MessageFormatter],
    settings: FromDishka[BotSettings],
    config: FromDishka[AppConfig],
) -> None:
    """Поиск с debug-трейсом — отправляет файл с полной цепочкой."""
    if not settings.aitunnel_api_key:
        await message.reply(formatter._t["llm_no_key"])
        return

    if not command.args:
        await message.reply(formatter._t["search_usage"])
        return

    if message.from_user is None:
        return

    thinking = await message.reply(formatter._t["search_thinking"])
    try:
        result = await llm_service.search_and_answer(
            message.from_user.id,
            message.chat.id,
            message.from_user.username,
            command.args,
            debug=True,
        )
        await _send_llm_response(thinking, result)

        if result.debug_trace:
            trace_file = BufferedInputFile(
                result.debug_trace.encode("utf-8"),
                filename="search_debug.txt",
            )
            await message.reply_document(trace_file, caption="Debug trace")
    except RateLimitExceeded:
        await thinking.edit_text(
            formatter._t["llm_rate_limit"].format(limit=config.llm.daily_limit_per_user),
        )
    except Exception:
        logger.exception("Search debug request failed")
        await thinking.edit_text(formatter._t["llm_error"])
