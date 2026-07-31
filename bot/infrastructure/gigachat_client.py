"""HTTP-клиент GigaChat API (Sber) — генерация картинок для /genai.

Документация:
  https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/post-token
  https://developers.sber.ru/docs/ru/gigachat/api/images-generation

Схема работы:
  1. POST {oauth_url} — обмен ключа авторизации (Base64 от "client_id:client_secret")
     на access_token. Токен живёт 30 минут и кэшируется в памяти клиента.
  2. POST {base_url}/chat/completions с "function_call": "auto" — модель сама
     решает вызвать встроенную функцию text2image и возвращает в content
     тег вида <img src="<file_id>" fuse="true"/>.
  3. GET {base_url}/files/{file_id}/content — бинарный JPEG.

Клиент живёт в APP-скоупе: один access_token и одна HTTP-сессия на весь процесс.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

import aiohttp

logger = logging.getLogger(__name__)

# Модель возвращает картинку тегом <img src="uuid" fuse="true"/> внутри content
_IMG_SRC_RE = re.compile(r'<img\s+src="([^"]+)"')

# Сколько секунд до истечения токена считать его протухшим
_TOKEN_LEEWAY = 60
# Если API не вернул expires_at — считаем, что токен живёт 30 минут
_TOKEN_DEFAULT_TTL = 30 * 60


class GigaChatError(Exception):
    """Любая ошибка при обращении к GigaChat API."""


class GigaChatNoImage(GigaChatError):
    """Модель ответила текстом, но картинку не нарисовала."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__("model returned no image")


class GigaChatClient:
    """Минимальный клиент GigaChat: авторизация + генерация картинки."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        oauth_url: str,
        scope: str,
        model: str,
        verify_ssl: bool = True,
        timeout: int = 120,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._oauth_url = oauth_url
        self._scope = scope
        self._model = model
        # aiohttp: True — обычная проверка сертификата, False — без проверки
        # (нужно, если в системе нет корневого сертификата Минцифры)
        self._ssl = bool(verify_ssl)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """Есть ли ключ авторизации. Без него команда /genai отключается."""
        return bool(self._api_key)

    # ── Инфраструктура ───────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _get_token(self, *, force: bool = False) -> str:
        """Возвращает access_token, при необходимости запрашивая новый."""
        async with self._token_lock:
            if not force and self._token and time.time() < self._token_expires_at:
                return self._token

            session = await self._get_session()
            headers = {
                "Authorization": f"Basic {self._api_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            try:
                async with session.post(
                    self._oauth_url,
                    headers=headers,
                    data={"scope": self._scope},
                    ssl=self._ssl,
                ) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        raise GigaChatError(f"oauth {resp.status}: {body[:200]}")
                    data = await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise GigaChatError(f"oauth request failed: {e}") from e

            token = data.get("access_token")
            if not token:
                raise GigaChatError("oauth: в ответе нет access_token")

            expires_at = float(data.get("expires_at") or 0)
            # API отдаёт метку в миллисекундах — приводим к секундам
            if expires_at > 1e11:
                expires_at /= 1000
            self._token = token
            self._token_expires_at = (
                expires_at - _TOKEN_LEEWAY if expires_at else time.time() + _TOKEN_DEFAULT_TTL - _TOKEN_LEEWAY
            )
            logger.info("gigachat: получен новый access_token (до %d)", int(self._token_expires_at))
            return token

    # ── Публичный API ────────────────────────────────────────────────

    async def generate_image(self, prompt: str, system_prompt: str) -> bytes:
        """Генерирует картинку по описанию. Возвращает JPEG-байты.

        Raises:
            GigaChatNoImage: модель ответила текстом вместо картинки.
            GigaChatError: сеть, авторизация или неожиданный ответ API.
        """
        if not self.configured:
            raise GigaChatError("GIGACHAT_API_KEY не задан")

        content = await self._chat(prompt, system_prompt)
        match = _IMG_SRC_RE.search(content)
        if match is None:
            raise GigaChatNoImage(content)
        return await self._download_file(match.group(1))

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Внутренние запросы (с одной повторной попыткой при 401) ──────

    async def _chat(self, prompt: str, system_prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            # Разрешаем модели самой вызвать встроенную функцию text2image
            "function_call": "auto",
        }
        session = await self._get_session()

        for attempt in (0, 1):
            token = await self._get_token(force=attempt == 1)
            try:
                async with session.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=payload,
                    ssl=self._ssl,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue  # токен протух — обновляем и пробуем ещё раз
                    text = await resp.text()
                    if resp.status != 200:
                        raise GigaChatError(f"chat {resp.status}: {text[:200]}")
                    data = await resp.json(content_type=None)
            except aiohttp.ClientError as e:
                raise GigaChatError(f"chat request failed: {e}") from e

            try:
                return data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError) as e:
                raise GigaChatError(f"chat: неожиданный формат ответа: {str(data)[:200]}") from e

        raise GigaChatError("chat: авторизация не удалась")

    async def _download_file(self, file_id: str) -> bytes:
        session = await self._get_session()

        for attempt in (0, 1):
            token = await self._get_token(force=attempt == 1)
            try:
                async with session.get(
                    f"{self._base_url}/files/{file_id}/content",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/jpg",
                    },
                    ssl=self._ssl,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        raise GigaChatError(f"file {resp.status}: {body}")
                    return await resp.read()
            except aiohttp.ClientError as e:
                raise GigaChatError(f"file request failed: {e}") from e

        raise GigaChatError("file: авторизация не удалась")
