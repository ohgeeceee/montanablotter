from dataclasses import asdict

from fastapi.testclient import TestClient

from services.agents.events_bus import AgentEvent
from services.agents.events_service import create_app


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.lists = {}
        self.sets = {}
        self.published = []

    def lpush(self, key, value):
        self.lists.setdefault(key, [])
        self.lists[key].insert(0, value)

    def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        self.lists[key] = items[start:end + 1]

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            end = len(items) - 1
        return items[start:end + 1]

    def sadd(self, key, *values):
        self.sets.setdefault(key, set())
        for value in values:
            self.sets[key].add(value)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def hset(self, key, mapping=None, **kwargs):
        self.store.setdefault(key, {})
        data = dict(mapping or {}, **kwargs)
        self.store[key].update(data)

    def hgetall(self, key):
        return dict(self.store.get(key, {}))

    def publish(self, channel, message):
        self.published.append((channel, message))

    def pubsub(self):
        raise NotImplementedError('pubsub not used in this test')


class TestAgentEventsService:
    def test_bootstrap_and_publish_endpoint_share_state(self):
        redis = FakeRedis()
        app = create_app(redis_client=redis)
        with TestClient(app) as client:
            bootstrap = client.get('/api/monitoring/agents/bootstrap')

            assert bootstrap.status_code == 200
            initial = bootstrap.json()['data']
            assert initial['ws_url'] == '/ws/agents'
            assert initial['approval_endpoint'] == '/api/monitoring/agents/reviews'

            response = client.post(
                '/api/monitoring/agents/events',
                json=asdict(
                    AgentEvent(
                        agent_id='hermes-intel',
                        agent_name='Hermes-Intel',
                        message='Queued 2 summaries for review',
                        stage='analyze',
                        status='processing',
                        created_at='2026-04-24T22:03:00Z',
                        active_alerts=2,
                    )
                ),
            )
            assert response.status_code == 200
            updated = client.get('/api/monitoring/agents/bootstrap').json()['data']
            assert updated['feed_items'][0]['message'] == 'Queued 2 summaries for review'
            assert updated['agents'][0]['agent_name'] == 'Hermes-Intel'
