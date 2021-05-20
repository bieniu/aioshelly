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
        reply = await self.ws_send(ws, 1, "Shelly.GetDeviceInfo")
        _LOGGER.info("Device info: %s", reply)
        reply = await self.ws_send(ws, 2, "Shelly.GetStatus")
        result = json.loads(reply)["result"]
        for k in result.keys():
            if k.startswith("switch:"):
                self.switches[k] = {}
        await self.ws_parse_switch(result)
        
        param_tmp = {}
        param_tmp["id"] = 1
        param_tmp["on"] = True
        reply = await self.ws_send(ws, 3, "Switch.Set", param_tmp)
        _LOGGER.info(reply)
        if await self.ws_event_handler(ws):
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
                params = json.loads(message)["params"]
                _LOGGER.info("Received event params: %s", params)
                await self.ws_parse_switch(params)
        except Exception as ex:
            _LOGGER.warning("Error on WS: %s, reinit", ex.code)
            return False

    async def ws_init(self, ip_dst):
        return await websockets.connect(f"ws://{ip_dst}/rpc")

    async def ws_send(self, ws, counter, method, params = None):
        msg_tmp = {}
        msg_tmp['id']= counter
        msg_tmp['src']= IDENT
        msg_tmp['method']= method
        if params:
            msg_tmp["params"] = params
        msg = json.dumps(msg_tmp)
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
