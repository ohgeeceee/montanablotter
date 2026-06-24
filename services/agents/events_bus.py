from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from redis import Redis
from redis.exceptions import RedisError

REDIS_HOST = os.getenv('MB_REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(os.getenv('MB_REDIS_PORT', '6379'))
REDIS_DB = int(os.getenv('MB_REDIS_DB', '0'))
REDIS_URL = os.getenv('MB_REDIS_URL', '').strip()
AGENT_EVENTS_CHANNEL = os.getenv('MB_AGENT_EVENTS_CHANNEL', 'agent-events')
AGENT_EVENTS_RECENT_KEY = os.getenv('MB_AGENT_EVENTS_RECENT_KEY', 'agent-events:recent')
AGENT_EVENTS_AGENT_INDEX_KEY = os.getenv('MB_AGENT_EVENTS_AGENT_INDEX_KEY', 'agent-events:agents')
AGENT_EVENTS_STATE_PREFIX = os.getenv('MB_AGENT_EVENTS_STATE_PREFIX', 'agent-events:state:')
AGENT_EVENTS_RECENT_LIMIT = int(os.getenv('MB_AGENT_EVENTS_RECENT_LIMIT', '200'))
AGENT_EVENTS_WS_URL = os.getenv('MB_AGENT_EVENTS_WS_URL', '/ws/agents')
AGENT_EVENTS_APPROVAL_ENDPOINT = os.getenv('MB_AGENT_EVENTS_APPROVAL_ENDPOINT', '/api/monitoring/agents/reviews')


@dataclass(slots=True)
class AgentEvent:
    agent_id: str
    agent_name: str
    message: str
    stage: str
    status: str
    created_at: str
    uptime_seconds: int | None = None
    last_sync_at: str | None = None
    active_alerts: int | None = None
    requires_approval: bool = False
    incident_id: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> 'AgentEvent':
        return cls(
            agent_id=str(data.get('agent_id') or ''),
            agent_name=str(data.get('agent_name') or ''),
            message=str(data.get('message') or ''),
            stage=str(data.get('stage') or ''),
            status=str(data.get('status') or ''),
            created_at=str(data.get('created_at') or _utcnow_iso()),
            uptime_seconds=_coerce_int(data.get('uptime_seconds')),
            last_sync_at=_optional_str(data.get('last_sync_at')),
            active_alerts=_coerce_int(data.get('active_alerts')),
            requires_approval=bool(data.get('requires_approval', False)),
            incident_id=_coerce_int(data.get('incident_id')),
            meta=dict(data.get('meta') or {}),
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_int(value: Any) -> int | None:
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value in (None, ''):
        return None
    return str(value)


def _redis_url() -> str:
    if REDIS_URL:
        return REDIS_URL
    return f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'


def create_redis_client(*, async_mode: bool = False):
    kwargs = dict(
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=3,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    if async_mode:
        from redis.asyncio import Redis as AsyncRedis
        return AsyncRedis.from_url(_redis_url(), **kwargs)
    return Redis.from_url(_redis_url(), **kwargs)


def serialize_agent_event(event: AgentEvent | dict[str, Any]) -> str:
    if isinstance(event, AgentEvent):
        payload = asdict(event)
    else:
        payload = dict(event)
    if not payload.get('created_at'):
        payload['created_at'] = _utcnow_iso()
    payload.setdefault('meta', {})
    return json.dumps(payload, separators=(',', ':'), sort_keys=True)


def deserialize_agent_event(payload: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', errors='replace')
    return json.loads(payload)


class RedisAgentEventHub:
    def __init__(
        self,
        redis_client: Redis | Any,
        *,
        channel: str = AGENT_EVENTS_CHANNEL,
        recent_key: str = AGENT_EVENTS_RECENT_KEY,
        agent_index_key: str = AGENT_EVENTS_AGENT_INDEX_KEY,
        state_prefix: str = AGENT_EVENTS_STATE_PREFIX,
        recent_limit: int = AGENT_EVENTS_RECENT_LIMIT,
    ) -> None:
        self.redis = redis_client
        self.channel = channel
        self.recent_key = recent_key
        self.agent_index_key = agent_index_key
        self.state_prefix = state_prefix
        self.recent_limit = max(1, int(recent_limit))

    def publish(self, event: AgentEvent | dict[str, Any]) -> dict[str, Any]:
        payload = event if isinstance(event, AgentEvent) else AgentEvent.from_mapping(event)
        encoded = serialize_agent_event(payload)
        mapping = self._state_mapping(payload)

        self.redis.lpush(self.recent_key, encoded)
        self.redis.ltrim(self.recent_key, 0, self.recent_limit - 1)
        self.redis.sadd(self.agent_index_key, payload.agent_id)
        self.redis.hset(f'{self.state_prefix}{payload.agent_id}', mapping=mapping)
        self.redis.publish(self.channel, encoded)
        return deserialize_agent_event(encoded)

    def load_recent_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = self.recent_limit if limit is None else max(1, int(limit))
        rows = self.redis.lrange(self.recent_key, 0, limit - 1)
        events = [deserialize_agent_event(row) for row in rows]
        return self._deduplicate_events(events)

    @staticmethod
    def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse repeated identical agent messages to reduce feed noise.

        Events are stored newest-first.  We keep only the newest event for each
        (agent_id, normalized message) pair in the recent window.  This prevents
        the workspace feed from being dominated by loops such as the news editor
        rejecting the same draft dozens of times over many minutes.
        """
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []

        for evt in events:
            agent_id = str(evt.get("agent_id") or "").strip()
            message = str(evt.get("message") or "").strip()
            key = (agent_id, message)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(evt)

        return deduped

    def load_agents(self) -> list[dict[str, Any]]:
        agent_ids = sorted(str(item) for item in self.redis.smembers(self.agent_index_key))
        agents: list[dict[str, Any]] = []
        for agent_id in agent_ids:
            if not agent_id.strip():
                continue
            state = self.redis.hgetall(f'{self.state_prefix}{agent_id}') or {}
            if not state:
                continue
            agents.append(self._state_to_agent(agent_id, state))
        agents.sort(key=lambda item: item.get('agent_name') or item.get('agent_id') or '')
        return agents

    def bootstrap_snapshot(self, *, limit: int | None = None) -> dict[str, Any]:
        try:
            agents = self.load_agents()
            feed_items = self.load_recent_events(limit=limit)
        except RedisError as exc:
            import logging
            logging.getLogger(__name__).error("Redis unavailable for bootstrap snapshot: %s", exc)
            agents, feed_items = [], []
        return {
            'connected': False,
            'generated_at': _utcnow_iso(),
            'agents': agents,
            'feed_items': feed_items,
            'channel': self.channel,
        }

    def _state_mapping(self, event: AgentEvent) -> dict[str, str]:
        data = asdict(event)
        mapping: dict[str, str] = {}
        for key, value in data.items():
            if key == 'meta':
                mapping[key] = json.dumps(value or {}, separators=(',', ':'), sort_keys=True)
            elif isinstance(value, bool):
                mapping[key] = '1' if value else '0'
            elif value is None:
                mapping[key] = ''
            else:
                mapping[key] = str(value)
        mapping['updated_at'] = _utcnow_iso()
        return mapping

    def _state_to_agent(self, agent_id: str, state: dict[str, Any]) -> dict[str, Any]:
        meta = state.get('meta') or '{}'
        try:
            meta_payload = json.loads(meta) if isinstance(meta, str) else dict(meta)
        except Exception:
            meta_payload = {}
        return {
            'agent_id': agent_id,
            'agent_name': state.get('agent_name') or agent_id,
            'message': state.get('message') or '',
            'stage': state.get('stage') or '',
            'status': state.get('status') or '',
            'created_at': state.get('created_at') or '',
            'uptime_seconds': _coerce_int(state.get('uptime_seconds')),
            'last_sync_at': state.get('last_sync_at') or None,
            'active_alerts': _coerce_int(state.get('active_alerts')),
            'requires_approval': str(state.get('requires_approval') or '').lower() in {'1', 'true', 'yes'},
            'incident_id': _coerce_int(state.get('incident_id')),
            'meta': meta_payload,
            'updated_at': state.get('updated_at') or '',
        }


def get_agent_events_hub() -> RedisAgentEventHub:
    return RedisAgentEventHub(create_redis_client())


def publish_agent_event(event: AgentEvent | dict[str, Any], *, redis_client: Redis | Any | None = None) -> dict[str, Any]:
    hub = RedisAgentEventHub(redis_client or create_redis_client())
    return hub.publish(event)


def build_agent_events_bootstrap(*, redis_client: Redis | Any | None = None, limit: int | None = None) -> dict[str, Any]:
    hub = RedisAgentEventHub(redis_client or create_redis_client())
    payload = hub.bootstrap_snapshot(limit=limit)
    payload['ws_url'] = AGENT_EVENTS_WS_URL
    payload['approval_endpoint'] = AGENT_EVENTS_APPROVAL_ENDPOINT
    return payload


def coerce_agent_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = AgentEvent.from_mapping(payload)
    return asdict(event)
