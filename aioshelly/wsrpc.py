"""WsRpc for Shelly."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class RPCError(Exception):
    """Base class for RPC errors."""


class RPCTimeout(RPCError):
    """Raised upon RPC call timeout."""


class JSONRPCError(RPCError):
    """Raised during RPC JSON parsing errors."""

    def __init__(self, code: int, message: str = ""):
        """Initialize JSON RPC errors."""
        self.code = code
        self.message = message
        super().__init__(code, message)


@dataclass
class RPCCall:
    """RPCCall class."""

    id: int
    method: str
    params: Any  # None or JSON-serializable
    src: str | None = None
    dst: str | None = None
    sent_at: datetime | None = None
    resolve: asyncio.Future | None = None

    @property
    def request_frame(self):
        """Request frame."""
        msg = {
            "id": self.id,
            "method": self.method,
            "src": self.src,
        }
        for obj in ("params", "dst"):
            if getattr(self, obj) is not None:
                msg[obj] = getattr(self, obj)
        return msg


class WsRPC:
    """WsRPC class."""

    def __init__(self, addr):
        """Initialize WsRPC class."""
        self.addr = addr
        self.rx_task = None
        self.websocket = None
        self.handlers = {}
        self.calls = {}
        self._call_id = 1
        self.src = f"aios-{id(self)}"
        self.dst = None

    @property
    def _next_id(self):
        self._call_id += 1
        return self._call_id

    # TODO: implement async iterator for notifications?
    async def on_notification(self, method, params):
        """Receive notification event."""
        _LOGGER.debug("Notification: %s %s", method, params)

    async def connect(self, aiohttp_session):
        """Connect to device."""
        if self.websocket:
            return  # already connected

        self.websocket = await aiohttp_session.ws_connect(f"http://{self.addr}/rpc")
        asyncio.create_task(self._recv())

        _LOGGER.info("Connected to %s", self.addr)

    async def disconnect(self):
        """Disconnect all sessions."""
        if self.websocket is None:
            return
        websocket, self.websocket = self.websocket, None
        await websocket.close()
        for call_item in self.calls.items():
            call_item.future.cancel()
        self.calls = {}

    async def _handle_call(self, frame):
        meth = frame["method"]
        if meth not in self.handlers:
            _LOGGER.debug("Peer tried to invoke %s", meth)
            self.websocket.send_json(
                {"id": frame["id"], "error": 400, "message": f"No handler for {meth}"}
            )
            return

        try:
            resp = await self.handlers[meth](frame.get("params", None))
        except JSONRPCError as jsonerr:
            self.websocket.send_json(
                {
                    "id": frame["id"],
                    "src": self.src,
                    "error": {"code": jsonerr.code, "message": jsonerr.message},
                }
            )
        except Exception as exc:
            self.websocket.send_json(
                {
                    "id": frame["id"],
                    "src": self.src,
                    "error": {"code": 500, "message": str(exc)},
                }
            )
        else:
            self.websocket.send_json({"id": frame["id"], "result": resp})

    async def _recv(self):
        async for msg in self.websocket:
            _LOGGER.debug("recv %s: %s", msg.type, msg.data)
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                _LOGGER.warning("%s disconnected", self)
                self.websocket = None
                return

            frame = msg.json()
            if peer_src := frame.get("src", None):
                if self.dst is not None and peer_src != self.dst:
                    _LOGGER.warning("Remote src changed: %s -> %s", self.dst, peer_src)
                self.dst = peer_src

            if meth := frame.get("method", None):
                # peer is invoking a method
                params = frame.get("params", None)
                if "id" in frame:
                    # and expects a response
                    asyncio.create_task(self._handle_call(frame))
                else:
                    # this is a notification
                    self.on_notification(meth, params)

            elif id_ := frame.get("id", None):
                # looks like a response
                if id_ not in self.calls:
                    _LOGGER.warning("Response for an unknown request id: %s", id_)
                    continue
                call = self.calls.pop(id_)
                call.resolve.set_result(frame)

            else:
                _LOGGER.warning("invalid frame: %s", frame)

    @property
    def connected(self):
        """Return connected state."""
        # TODO: this is quite naive, ws may be in limbo
        return self.websocket is not None

    async def call(self, method, params=None, timeout=10):
        """Websocket RPC call."""
        call = RPCCall(self._next_id, method, params)
        call.resolve = asyncio.Future()
        call.src = self.src
        call.dst = self.dst

        self.calls[call.id] = call
        await self.websocket.send_json(call.request_frame)
        call.sent_at = datetime.utcnow()

        try:
            resp = await asyncio.wait_for(call.resolve, timeout)
        except asyncio.TimeoutError as exc:
            _LOGGER.warning("%s timed out: %s", call, exc)
            raise RPCTimeout(call) from exc
        except Exception as exc:
            _LOGGER.error("%s ???: %s", call, exc)
            raise RPCError(call, exc) from exc

        if "result" in resp:
            _LOGGER.debug("%s(%s) -> %s", call.method, call.params, resp["result"])
            return resp["result"]

        try:
            code, msg = resp["error"]["code"], resp["error"]["message"]
            raise JSONRPCError(code, msg)
        except KeyError as err:
            raise RPCError(f"bad response: {resp}") from err
