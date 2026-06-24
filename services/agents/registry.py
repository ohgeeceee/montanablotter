from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from services.agents.status import system_snapshot

# Re-use existing OpenClaw log parser so status matches the command-center feed.
from blueprints.admin.agents import _agent_snapshot as _openclaw_agent_snapshot, KNOWN_AGENTS


OPENCLAW_CONFIG_PATH = Path('/root/.openclaw/openclaw.json')
HERMES_CONFIG_PATH = Path('/root/.hermes/config.yaml')
HERMES_PROFILES_DIR = Path('/root/.hermes/profiles')


@dataclass
class AgentEntry:
    id: str
    name: str
    source: str
    status: str = 'unknown'
    last_seen: str = ''
    url: str = ''
    capabilities: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding='utf-8') as fh:
            return yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _openclaw_agents() -> list[AgentEntry]:
    cfg = _load_json(OPENCLAW_CONFIG_PATH)
    agents = cfg.get('agents', {}).get('list', [])
    snapshot = _openclaw_agent_snapshot()
    live = snapshot.get('agents', {})

    entries: list[AgentEntry] = []
    for agent in agents:
        aid = agent.get('id') or agent.get('name')
        name = agent.get('name') or aid
        if not aid:
            continue
        live_info = live.get(aid, {})
        status = live_info.get('status', 'unknown')
        last_seen = live_info.get('last_seen', '')
        entries.append(
            AgentEntry(
                id=f'openclaw:{aid}',
                name=name,
                source='openclaw',
                status=status,
                last_seen=last_seen,
                url=f'/admin/office/3d?agent={aid}&runtime=openclaw',
                capabilities=['chat', 'tool-use'],
                meta={'model': agent.get('model'), 'workspace': agent.get('workspace')},
            )
        )
    return entries


def _montana_blotter_agents() -> list[AgentEntry]:
    snapshot = _openclaw_agent_snapshot()
    live = snapshot.get('agents', {})

    entries: list[AgentEntry] = []
    for aid in KNOWN_AGENTS:
        live_info = live.get(aid, {})
        status = live_info.get('status', 'unknown')
        last_seen = live_info.get('last_seen', '')
        entries.append(
            AgentEntry(
                id=f'blotter:{aid}',
                name=aid,
                source='montana-blotter',
                status=status,
                last_seen=last_seen,
                url=f'/admin/command-center?agent={aid}',
                capabilities=['pipeline', 'monitoring'],
            )
        )
    return entries


def _hermes_profiles() -> list[AgentEntry]:
    cfg = _load_yaml(HERMES_CONFIG_PATH)
    default_personality = cfg.get('display', {}).get('personality', 'helpful')

    profiles: list[AgentEntry] = []
    if HERMES_PROFILES_DIR.is_dir():
        for profile_dir in sorted(HERMES_PROFILES_DIR.iterdir()):
            if not profile_dir.is_dir() or profile_dir.name.startswith('.'):
                continue
            name = profile_dir.name
            profile_cfg = _load_yaml(profile_dir / 'config.yaml') if (profile_dir / 'config.yaml').exists() else {}
            personality = profile_cfg.get('display', {}).get('personality', default_personality)
            profiles.append(
                AgentEntry(
                    id=f'hermes:{name}',
                    name=name,
                    source='hermes',
                    status='active',
                    url=f'/hermes/?profile={name}',
                    capabilities=['chat', 'orchestrate'],
                    meta={'personality': personality},
                )
            )

    # Always include a default Hermes runtime entry.
    profiles.insert(
        0,
        AgentEntry(
            id='hermes:default',
            name='Hermes',
            source='hermes',
            status='active',
            url='/hermes/',
            capabilities=['chat', 'orchestrate', 'code-execution'],
            meta={'default_personality': default_personality},
        ),
    )
    return profiles


def build_registry() -> dict[str, Any]:
    """Return a unified agent registry across OpenClaw, Montana Blotter, and Hermes."""
    agents: list[AgentEntry] = []
    agents.extend(_openclaw_agents())
    agents.extend(_montana_blotter_agents())
    agents.extend(_hermes_profiles())

    sys = system_snapshot()
    services = sys.get('services') or []
    queues = sys.get('queues') or []
    queue_depth = 0
    if isinstance(queues, list):
        queue_depth = sum(int(q.get('count', 0) or 0) for q in queues if isinstance(q, dict))

    return {
        'captured_at': _now_iso(),
        'agents': [a.__dict__ for a in agents],
        'system': {
            'services': services,
            'queue_depth': queue_depth,
            'alerts': sys.get('alerts', []),
        },
    }
