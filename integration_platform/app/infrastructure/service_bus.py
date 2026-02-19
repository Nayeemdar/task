"""
Azure Service Bus connector.

Bridges the internal EventBus to Azure Service Bus so that:
  - Every IntegrationEvent published in-process is forwarded to a Service Bus topic.
  - The on-premise Service Bus Processor receives events via a subscription.

When AZURE_SERVICE_BUS_CONNECTION_STRING is not set the connector operates in
no-op mode so local development works without Azure credentials.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Coroutine, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    from azure.servicebus.aio import ServiceBusClient, ServiceBusSender
    from azure.servicebus import ServiceBusMessage
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    logger.warning("azure-servicebus not installed — Service Bus bridge disabled")


class ServiceBusBridge:
    """
    Publishes IntegrationEvents to Azure Service Bus topic.

    Usage:
        bridge = ServiceBusBridge()
        await bridge.connect()
        await bridge.send_event(event)
        await bridge.close()
    """

    def __init__(self):
        self._connection_string = settings.AZURE_SERVICE_BUS_CONNECTION_STRING
        self._topic_name = settings.AZURE_SERVICE_BUS_TOPIC_NAME
        self._client: Optional[object] = None
        self._sender: Optional[object] = None
        self._enabled = bool(self._connection_string and AZURE_AVAILABLE)

    async def connect(self) -> None:
        if not self._enabled:
            logger.info("ServiceBusBridge: running in no-op mode (no connection string or SDK)")
            return
        self._client = ServiceBusClient.from_connection_string(self._connection_string)
        self._sender = self._client.get_topic_sender(topic_name=self._topic_name)
        await self._sender.__aenter__()
        logger.info("ServiceBusBridge connected to topic '%s'", self._topic_name)

    async def close(self) -> None:
        if self._sender:
            await self._sender.__aexit__(None, None, None)
        if self._client:
            await self._client.__aexit__(None, None, None)

    async def send_event(self, event_dict: dict) -> None:
        if not self._enabled:
            logger.debug("ServiceBusBridge(no-op): would send event %s", event_dict.get("event_id"))
            return
        try:
            message = ServiceBusMessage(
                body=json.dumps(event_dict),
                content_type="application/json",
                subject=event_dict.get("event_type", ""),
            )
            await self._sender.send_messages(message)
            logger.debug("Event sent to Service Bus: %s", event_dict.get("event_id"))
        except Exception as exc:
            logger.error("Failed to send event to Service Bus: %s", exc)


class ServiceBusProcessor:
    """
    Receives messages from an Azure Service Bus queue and invokes a handler.

    Used by the on-premise processor component to consume cloud-side messages.
    """

    def __init__(self, handler: Callable[[dict], Coroutine]):
        self._connection_string = settings.AZURE_SERVICE_BUS_CONNECTION_STRING
        self._queue_name = settings.AZURE_SERVICE_BUS_QUEUE_NAME
        self._handler = handler
        self._enabled = bool(self._connection_string and AZURE_AVAILABLE)
        self._running = False

    async def start(self) -> None:
        if not self._enabled:
            logger.info("ServiceBusProcessor: no-op mode")
            return

        self._running = True
        async with ServiceBusClient.from_connection_string(self._connection_string) as client:
            async with client.get_queue_receiver(self._queue_name) as receiver:
                logger.info("ServiceBusProcessor listening on queue '%s'", self._queue_name)
                async for message in receiver:
                    if not self._running:
                        break
                    try:
                        body = json.loads(str(message))
                        await self._handler(body)
                        await receiver.complete_message(message)
                    except Exception as exc:
                        logger.error("ServiceBusProcessor handler error: %s", exc)
                        await receiver.abandon_message(message)

    async def stop(self) -> None:
        self._running = False


# ── Module-level bridge singleton used by EventBus subscriber ─────────────────

_bridge: Optional[ServiceBusBridge] = None


async def get_bridge() -> ServiceBusBridge:
    global _bridge
    if _bridge is None:
        _bridge = ServiceBusBridge()
        await _bridge.connect()
    return _bridge
