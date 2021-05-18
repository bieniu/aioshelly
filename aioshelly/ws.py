import json
import logging
from typing import Any, TypedDict

import websockets

_LOGGER = logging.getLogger(__name__)

IDENT = "ha-shelly-test"


class WsSwitch(TypedDict):

    id: int
    aenergy: Any
    apower: Any
    output: bool
    source: str
    voltage: int


class WebSocketShelly:
    def __init__(self):
        """Initialize a coap message."""
        self.switches = {}

    async def initialize(self, device_ip: str):
        """Initialize the COAP manager."""
        ws = await self.ws_init(device_ip)
        reply = await self.ws_send(ws, "Shelly.GetDeviceInfo", 1)
        _LOGGER.info("Device info: %s", reply)
        reply = await self.ws_send(ws, "Shelly.GetStatus", 2)
        x = json.loads(reply)["result"]
        for k in x.keys():
            if k.startswith("switch:"):
                self.switches[k] = {}
        await self.ws_parse_switch(x)
        v = await self.ws_event_handler(ws)
        if not v:
            await self.initialize(device_ip)

    async def ws_parse_switch(self, json_reply):
        for i in range(len(self.switches)):
            sw = "switch:" + str(i)
            ws_sw: WsSwitch = json_reply.get(sw)
            if ws_sw is not None:
                self.switches[sw].update(ws_sw)
                await self.ws_sw_log(self.switches[sw])

    async def ws_event_handler(self, websocket):
        try:
            async for message in websocket:
                x = json.loads(message)["params"]
                _LOGGER.info("Received event params: %s", x)
                await self.ws_parse_switch(x)
        except Exception as ex:
            _LOGGER.warning("Error on WS: %s, reinit", ex.code)
            return False

    async def ws_init(self, ip_dst):
        return await websockets.connect(f"ws://{ip_dst}/rpc")

    async def ws_send(self, ws, method, counter):
        msg = (
            '{"id": '
            + str(counter)
            + ', "src": "'
            + IDENT
            + '", "method": "'
            + method
            + '"}'
        )
        _LOGGER.info("Sending request: %s", msg)
        await ws.send(msg)
        return await ws.recv()

    async def ws_sw_log(self, sw: WsSwitch):

        _LOGGER.info(
            "\n          id: %s\n          output: %s\n          source: %s\n          voltage: %s\n          apower: %s\n          aenergy: %s",
            sw.get("id"),
            sw.get("output"),
            sw.get("source"),
            sw.get("voltage"),
            sw.get("apower"),
            sw.get("aenergy"),
        )
