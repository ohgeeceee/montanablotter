from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from agent_events_bus import (
    AGENT_EVENTS_APPROVAL_ENDPOINT,
    AGENT_EVENTS_CHANNEL,
    AGENT_EVENTS_WS_URL,
    AgentEvent,
    RedisAgentEventHub,
    build_agent_events_bootstrap,
    create_redis_client,
    publish_agent_event,
)


def _build_runtime_clients(
    *,
    redis_client: Any | None = None,
    async_redis_client: Any | None = None,
) -> tuple[Any, Any, RedisAgentEventHub]:
    redis_sync = redis_client or create_redis_client(async_mode=False)
    if async_redis_client is not None:
        redis_async = async_redis_client
    elif redis_client is not None:
        redis_async = redis_client
    else:
        redis_async = create_redis_client(async_mode=True)
    return redis_sync, redis_async, RedisAgentEventHub(redis_sync)


async def _stream_redis_events(websocket: WebSocket, redis_client: Any, channel: str) -> None:
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get('type') == 'message':
                payload = message.get('data')
                if isinstance(payload, bytes):
                    payload = payload.decode('utf-8', errors='replace')
                await websocket.send_text(payload)
            await asyncio.sleep(0.05)
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.close()


def create_app(*, redis_client: Any | None = None, async_redis_client: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_sync, runtime_async, runtime_hub = _build_runtime_clients(
            redis_client=redis_client,
            async_redis_client=async_redis_client,
        )
        app.state.redis_sync = runtime_sync
        app.state.redis_async = runtime_async
        app.state.hub = runtime_hub
        yield
        close = getattr(runtime_async, 'aclose', None)
        if callable(close):
            await close()

    app = FastAPI(title='Hermes Agent Events', lifespan=lifespan)
    app.state.redis_sync = None
    app.state.redis_async = None
    app.state.hub = None

    @app.get('/api/monitoring/agents/bootstrap')
    async def bootstrap() -> dict[str, Any]:
        payload = build_agent_events_bootstrap(redis_client=app.state.redis_sync)
        payload['ws_url'] = AGENT_EVENTS_WS_URL
        payload['approval_endpoint'] = AGENT_EVENTS_APPROVAL_ENDPOINT
        return {'ok': True, 'data': payload}

    @app.post('/api/monitoring/agents/events')
    async def publish_event(request: Request) -> dict[str, Any]:
        body = await request.json()
        payload = publish_agent_event(body, redis_client=app.state.redis_sync)
        snapshot = build_agent_events_bootstrap(redis_client=app.state.redis_sync)
        snapshot['ws_url'] = AGENT_EVENTS_WS_URL
        snapshot['approval_endpoint'] = AGENT_EVENTS_APPROVAL_ENDPOINT
        return {'ok': True, 'data': snapshot, 'event': payload}

    @app.websocket('/ws/agents')
    async def websocket_agents(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({
            'type': 'snapshot',
            **build_agent_events_bootstrap(redis_client=app.state.redis_sync),
        })
        try:
            stream_task = asyncio.create_task(_stream_redis_events(websocket, app.state.redis_async, AGENT_EVENTS_CHANNEL))
            receive_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait(
                {stream_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
        except WebSocketDisconnect:
            return
        except Exception:
            raise

    return app


app = create_app()
