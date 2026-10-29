"""
Connection to Home Assistant through the Supervisor.

- language of Home Assistant (for the "automatic" UI language)
- entities (sensor.stream_recorder_*, binary_sensor.stream_recorder_recording)
- events (stream_recorder_finished, stream_recorder_error)
- commands via the action hassio.app_stdin (JSON on standard input)

Everything here is optional: without SUPERVISOR_TOKEN (local tests) nothing is sent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Callable

import httpx

log = logging.getLogger("stream-recorder")


class Supervisor:
    def __init__(self, url: str, token: str | None):
        self.url = url.rstrip("/")
        self.token = token or ""
        self.client: httpx.AsyncClient | None = None
        self.last: dict[str, Any] = {}       # entity_id -> last payload (only send changes)
        self.last_sent = 0.0
        self.ok: bool | None = None          # last call worked?
        self.lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {self.token}"})
        return self.client

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        if not self.enabled:
            return None
        try:
            r = await self._client().request(method, f"{self.url}/core/api{path}", json=payload)
            r.raise_for_status()
            if self.ok is False:
                log.info("Home Assistant API reachable again")
            self.ok = True
            return r.json() if r.content else {}
        except (httpx.HTTPError, ValueError) as e:
            if self.ok is not False:
                log.warning("Home Assistant API not reachable (%s %s): %s", method, path, e)
            self.ok = False
            return None

    async def language(self) -> str | None:
        cfg = await self._call("GET", "/config")
        return (cfg or {}).get("language")

    async def set_states(self, states: dict[str, tuple[str, dict]], force: bool = False) -> None:
        """states: entity_id -> (state, attributes). Only changed entities are sent unless force."""
        if not self.enabled:
            return
        async with self.lock:
            for eid, (state, attrs) in states.items():
                payload = {"state": state, "attributes": attrs}
                if not force and self.last.get(eid) == payload:
                    continue
                if await self._call("POST", f"/states/{eid}", payload) is not None:
                    self.last[eid] = payload

    async def fire(self, event: str, data: dict) -> None:
        if await self._call("POST", f"/events/{event}", data) is not None:
            log.debug("Event %s sent", event)


class StdinReader:
    """Reads JSON values from standard input (hassio.app_stdin) in a thread.

    Home Assistant encodes the `input` of the action as JSON and the Supervisor writes it
    to stdin as it is, without a guaranteed line break. Therefore the stream is decoded
    value by value with raw_decode instead of line by line.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, handler: Callable[[Any], Any], fd: int = 0):
        self.loop, self.handler, self.fd = loop, handler, fd

    def start(self) -> None:
        threading.Thread(target=self._run, name="stdin", daemon=True).start()

    def _run(self) -> None:
        dec = json.JSONDecoder()
        buf = ""
        while True:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return                           # EOF: no stdin attached
            buf += chunk.decode("utf-8", "replace")
            while True:
                buf = buf.lstrip()
                if not buf:
                    break
                try:
                    value, end = dec.raw_decode(buf)
                except json.JSONDecodeError:
                    if "\n" in buf and buf[0] not in "{[\"":   # plain text line, e.g. stop
                        line, buf = buf.split("\n", 1)
                        asyncio.run_coroutine_threadsafe(self.handler(line.strip()), self.loop)
                        continue
                    if len(buf) > 100_000:       # garbage: start over
                        log.warning("stdin: discarded %d characters that are not JSON", len(buf))
                        buf = ""
                    break
                buf = buf[end:]
                asyncio.run_coroutine_threadsafe(self.handler(value), self.loop)
