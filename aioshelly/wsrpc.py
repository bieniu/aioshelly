from __future__ import annotations

import asyncio
import logging
import sys
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
        self.code = code
        self.message = message
        super().__init__(code, message)


@dataclass
class RPCCall:
    id: int
    method: str
    params: Any  # None or JSON-serializable
    src: str | None = None
    dst: str | None = None
    sent_at: datetime | None = None
    resolve: asyncio.Future | None = None

    @property
    def request_frame(self):
        msg = {
            "id": self.id,
            "method": self.method,
            "src": self.src,
        }
        for o in ("params", "dst"):
            if getattr(self, o) is not None:
                msg[o] = getattr(self, o)
        return msg


class WsRPC:
    def __init__(self, addr):
        self.addr = addr
        self.rx_task = None
        self.ws = None
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
    def on_notification(self, method, params):
        _LOGGER.debug(f"Notification: {method} {params}")

    async def connect(self, aiohttp_serssion):
        if self.ws:
            return  # already connected

        self.ws = await aiohttp_serssion.ws_connect(f"http://{self.addr}/rpc")
        asyncio.create_task(self._recv())

        _LOGGER.info(f"Connected to {self.addr}")

    async def disconnect(self):
        if self.ws is None:
            return
        ws, self.ws = self.ws, None
        await ws.close()
        for i, c in self.calls.items():
            c.future.cancel()
        self.calls = {}

    async def _handle_call(self, frame):
        meth = frame["method"]
        if meth not in self.handlers:
            _LOGGER.debug(f"Peer tried to invoke {meth}")
            self.ws.send_json(
                {"id": frame["id"], "error": 400, "message": f"No handler for {meth}"}
            )
            return

        try:
            resp = await self.handlers[meth](frame.get("params", None))
        except JSONRPCError as je:
            self.ws.send_json(
                {
                    "id": frame["id"],
                    "src": self.src,
                    "error": {"code": je.code, "message": je.message},
                }
            )
        except Exception as exc:
            self.ws.send_json(
                {
                    "id": frame["id"],
                    "src": self.src,
                    "error": {"code": 500, "message": str(exc)},
                }
            )
        else:
            self.ws.send_json({"id": frame["id"], "result": resp})

    async def _recv(self):
        async for msg in self.ws:
            _LOGGER.debug(f"recv {msg.type}: {msg.data}")
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                _LOGGER.warning(f"{self} disconnected")
                self.ws = None
                return

            frame = msg.json()
            if peer_src := frame.get("src", None):
                if self.dst is not None and peer_src != self.dst:
                    _LOGGER.warning(f"Remote src changed: {self.dst} -> {peer_src}")
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
                    _LOGGER.warning(f"Response for an unknown request id: {id_}")
                    continue
                call = self.calls.pop(id_)
                call.resolve.set_result(frame)

            else:
                _LOGGER.warning(f"invalid frame: {frame}")

    @property
    def connected(self):
        # XXX: this is quite naive, ws may be in limbo
        return self.ws is not None

    async def call(self, method, params=None, timeout=10):
        call = RPCCall(self._next_id, method, params)
        call.resolve = asyncio.Future()
        call.src = self.src
        call.dst = self.dst

        self.calls[call.id] = call
        await self.ws.send_json(call.request_frame)
        call.sent_at = datetime.utcnow()

        try:
            resp = await asyncio.wait_for(call.resolve, timeout)
        except asyncio.TimeoutError as exc:
            _LOGGER.warning(f"{call} timed out: {exc}")
            raise RPCTimeout(call)
        except Exception as exc:
            _LOGGER.error(f"{call} ???: {exc}")
            raise RPCError(call, exc)

        if "result" in resp:
            _LOGGER.debug(f"{call.method}({call.params or ''}) -> {resp['result']}")
            return resp["result"]

        try:
            code, msg = resp["error"]["code"], resp["error"]["message"]
            raise JSONRPCError(code, msg)
        except KeyError as err:
            raise RPCError(f"bad response: {resp}") from err


async def main():
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)-15s %(message)s")
    async with aiohttp.ClientSession() as session:
        shelly = WsRPC(sys.argv[1])
        await shelly.connect(session)
        await shelly.call("Shelly.GetDeviceInfo")
        await shelly.call("Shelly.GetStatus")
        while True:
            await asyncio.sleep(10)
            await shelly.call("Shelly.GetStatus")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
