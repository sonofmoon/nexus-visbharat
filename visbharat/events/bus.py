import logging
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger('nvb.events')

class EventBus:
    def __init__(self):
        self._subscribers = defaultdict(list)
        self._history = []

    def subscribe(self, event_type: str, callback):
        self._subscribers[event_type].append(callback)

    def publish(self, event_type: str, payload: dict, actor: str = 'system'):
        event_data = {
            'event_type': event_type,
            'payload': payload,
            'actor': actor,
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        }
        self._history.append(event_data)
        if len(self._history) > 1000:
            self._history.pop(0)

        listeners = self._subscribers.get(event_type, [])
        for cb in listeners:
            try:
                cb(event_data)
            except Exception as e:
                logger.error(f"Error executing event bus listener for {event_type}: {e}")

        return event_data

    def get_recent_events(self, limit=50):
        return self._history[-limit:]

event_bus = EventBus()
