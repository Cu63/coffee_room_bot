"""Ошибка LLM API видна с причиной: исключение несёт тело ответа, а не одно «400, Bad Request»."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.infrastructure.aitunnel_client import AiTunnelClient


async def test_api_error_carries_the_response_body():
    async def completions(request: web.Request) -> web.Response:
        return web.json_response({"error": {"message": "context length exceeded"}}, status=400)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    async with TestServer(app) as server:
        client = AiTunnelClient("key", str(server.make_url("/v1")), "model", 100)
        try:
            with pytest.raises(aiohttp.ClientResponseError) as error:
                await client.chat([{"role": "user", "content": "привет"}])
        finally:
            await client.close()

    # Тип и статус прежние — меняется только текст: в нём теперь причина
    assert error.value.status == 400
    assert "context length exceeded" in str(error.value)
