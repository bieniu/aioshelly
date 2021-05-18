import asyncio
import logging
from aioshelly.ws import WebSocketShelly

logging.basicConfig(level=logging.INFO)


async def test_run(device_ip):
    ws = WebSocketShelly()
    await ws.initialize(device_ip)


asyncio.get_event_loop().run_until_complete(test_run("192.168.188.39"))
