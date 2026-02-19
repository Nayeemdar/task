"""
Lightweight HTTP server — /health and /metrics endpoints.

Design goals
────────────
• Works on Windows Server (no Unix sockets, no SIGPIPE, no fork).
• Uses only asyncio + the standard library http.server — zero extra deps
  beyond prometheus_client (already in requirements).
• Runs as a background asyncio task alongside the main on-prem process;
  never blocks event processing.

Endpoints
─────────
GET /health   → 200 JSON  {"status": "ok", "uptime_s": <float>}
              → 503 JSON  {"status": "starting"} during startup
GET /metrics  → 200 text/plain  (Prometheus exposition format)
GET /          → 404

Usage
─────
    server = HealthServer(host="0.0.0.0", port=9090)
    task = asyncio.create_task(server.serve(), name="health-server")
    ...
    task.cancel()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

logger = logging.getLogger(__name__)

_STARTUP_SENTINEL = object()  # sentinel so callers can signal "ready"


class HealthServer:
    """
    asyncio-based HTTP server for /health and /metrics.

    Parameters
    ----------
    host : str
        Interface to bind (default ``"0.0.0.0"``).
    port : int
        TCP port (default ``9090``).
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9090):
        self._host = host
        self._port = port
        self._start_time: float = time.monotonic()
        self._ready: bool = False

    def mark_ready(self) -> None:
        """Call once the on-prem process has finished startup."""
        self._ready = True

    async def serve(self) -> None:
        """
        Start the asyncio TCP server and handle connections until cancelled.
        Windows-safe: uses asyncio.start_server, not unix sockets.
        """
        server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
        )
        addr = server.sockets[0].getsockname() if server.sockets else (self._host, self._port)
        logger.info("HealthServer listening on %s:%s", addr[0], addr[1])
        try:
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            logger.info("HealthServer stopped")

    # ── Connection / request handling ────────────────────────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        except asyncio.TimeoutError:
            writer.close()
            return

        request_line = raw.decode("utf-8", errors="replace").split("\r\n", 1)[0]
        parts = request_line.split()
        method = parts[0] if parts else "GET"
        path = parts[1].split("?")[0] if len(parts) >= 2 else "/"

        if method != "GET":
            await self._send(writer, 405, "text/plain", b"Method Not Allowed")
        elif path == "/health":
            await self._handle_health(writer)
        elif path == "/metrics":
            await self._handle_metrics(writer)
        else:
            await self._send(writer, 404, "text/plain", b"Not Found")

        writer.close()
        await writer.wait_closed()

    async def _handle_health(self, writer: asyncio.StreamWriter) -> None:
        uptime = time.monotonic() - self._start_time
        if self._ready:
            body = json.dumps({"status": "ok", "uptime_s": round(uptime, 1)}).encode()
            await self._send(writer, 200, "application/json", body)
        else:
            body = json.dumps({"status": "starting", "uptime_s": round(uptime, 1)}).encode()
            await self._send(writer, 503, "application/json", body)

    async def _handle_metrics(self, writer: asyncio.StreamWriter) -> None:
        data = generate_latest()  # bytes
        await self._send(writer, 200, CONTENT_TYPE_LATEST, data)

    @staticmethod
    async def _send(
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: bytes,
    ) -> None:
        reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 503: "Service Unavailable"}.get(
            status, "Unknown"
        )
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            "\r\n"
        ).encode() + body
        writer.write(response)
        await writer.drain()
