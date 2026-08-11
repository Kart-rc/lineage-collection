from __future__ import annotations

from collections.abc import Callable

from lineage_api.application.models import OutboxEvent
from lineage_api.application.ports import ClockPort, LaneBrokerPort, OutboxPort


class OutboxDispatcher:
    """Publishes committed outbox events with stable broker deduplication identities."""

    def __init__(
        self,
        outbox: OutboxPort,
        broker: LaneBrokerPort,
        clock: ClockPort,
        *,
        after_publish: Callable[[OutboxEvent], None] | None = None,
    ) -> None:
        self.outbox = outbox
        self.broker = broker
        self.clock = clock
        self.after_publish = after_publish

    def dispatch(self, limit: int = 100) -> int:
        if limit < 1:
            raise ValueError("dispatch limit must be positive")
        delivered = 0
        for event in self.outbox.pending(limit):
            self.broker.publish(
                event.topic,
                event.partition_key,
                event.payload_ref,
                event.correlation_id,
                message_id=event.outbox_id,
            )
            if self.after_publish is not None:
                self.after_publish(event)
            self.outbox.mark_delivered(event.outbox_id, self.clock.now())
            delivered += 1
        return delivered
