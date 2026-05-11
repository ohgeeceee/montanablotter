import json

from services.agents.events_bus import AgentEvent, RedisAgentEventHub


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


class TestAgentEventsBus:
    def test_publish_updates_recent_feed_and_latest_state(self):
        redis = FakeRedis()
        hub = RedisAgentEventHub(redis_client=redis, recent_limit=10)

        first = AgentEvent(
            agent_id='hermes-scan',
            agent_name='Hermes-Scan',
            message='Found 3 new bookings in Cascade County',
            stage='scrape',
            status='success',
            created_at='2026-04-24T22:00:00Z',
            uptime_seconds=120,
            last_sync_at='2026-04-24T22:00:00Z',
            active_alerts=0,
        )
        second = AgentEvent(
            agent_id='hermes-scan',
            agent_name='Hermes-Scan',
            message='Found 4 new bookings in Cascade County',
            stage='scrape',
            status='success',
            created_at='2026-04-24T22:01:00Z',
            uptime_seconds=180,
            last_sync_at='2026-04-24T22:01:00Z',
            active_alerts=1,
        )

        hub.publish(first)
        hub.publish(second)
        snapshot = hub.bootstrap_snapshot()

        assert redis.published[0][0] == 'agent-events'
        assert json.loads(redis.published[1][1])['message'] == 'Found 4 new bookings in Cascade County'
        assert snapshot['feed_items'][0]['message'] == 'Found 4 new bookings in Cascade County'
        assert snapshot['agents'][0]['active_alerts'] == 1
        assert snapshot['agents'][0]['uptime_seconds'] == 180

    def test_snapshot_keeps_multiple_agents(self):
        redis = FakeRedis()
        hub = RedisAgentEventHub(redis_client=redis, recent_limit=10)

        hub.publish(
            AgentEvent(
                agent_id='hermes-scan',
                agent_name='Hermes-Scan',
                message='Scan cycle complete',
                stage='scrape',
                status='success',
                created_at='2026-04-24T22:00:00Z',
            )
        )
        hub.publish(
            AgentEvent(
                agent_id='hermes-notify',
                agent_name='Hermes-Notify',
                message='Alert queued',
                stage='notify',
                status='processing',
                created_at='2026-04-24T22:01:00Z',
            )
        )

        snapshot = hub.bootstrap_snapshot()
        agent_names = {item['agent_name'] for item in snapshot['agents']}
        assert agent_names == {'Hermes-Scan', 'Hermes-Notify'}
        assert snapshot['feed_items'][0]['agent_id'] == 'hermes-notify'
