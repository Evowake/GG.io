import asyncio
import random
import ssl
import json
import time
import uuid
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
from fake_useragent import UserAgent
import aiohttp

# Configuration
WSS_URI = "wss://proxy.wynd.network:4650/"
SERVER_HOSTNAME = "proxy.wynd.network"
PROXY_LIST_FILE = "proxy_list.txt"
SUPER_PROXY_FILE = "super_proxy.txt"
USER_ID_FILE = "user_id.txt"
PROXY_LIST_URL = "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/socks5.txt"

# Initialize UserAgent for random user agent generation
user_agent = UserAgent()

async def fetch_external_proxies():
    async with aiohttp.ClientSession() as session:
        async with session.get(PROXY_LIST_URL) as response:
            return await response.text()

async def remove_proxy_from_file(file_path, proxy):
    logger.info(f"Removing {proxy} from {file_path}")
    if proxy.startswith("socks5://"):
        proxy = proxy[len("socks5://"):] # Remove "socks5://" prefix

    try:
        with open(file_path, "r") as file:
            proxies = file.readlines()
        
        with open(file_path, "w") as file:
            for p in proxies:
                if p.strip() != proxy:
                    file.write(p)
        
        logger.info(f"{proxy} removed from {file_path}")
    except Exception as e:
        logger.error(f"Error removing {proxy} from {file_path}: {e}")

async def connect_to_wss(socks5_proxy, user_id):
    # Prepend "socks5://" to the proxy string if it's not already there
    if not socks5_proxy.startswith("socks5://"):
        socks5_proxy = 'socks5://' + socks5_proxy

    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    logger.info(f"Device ID: {device_id}")
    while True:
        try:
            await asyncio.sleep(random.randint(1, 10) / 10)
            custom_headers = {"User-Agent": user_agent.random}
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            proxy = Proxy.from_url(socks5_proxy)
            async with proxy_connect(
                WSS_URI,
                proxy=proxy,
                ssl=ssl_context,
                server_hostname=SERVER_HOSTNAME,
                extra_headers=custom_headers,
            ) as websocket:

                async def send_ping():
                    while True:
                        send_message = json.dumps(
                            {
                                "id": str(uuid.uuid4()),
                                "version": "1.0.0",
                                "action": "PING",
                                "data": {},
                            }
                        )
                        logger.debug(f"Sending PING: {send_message}")
                        await websocket.send(send_message)
                        await asyncio.sleep(20)

                await asyncio.sleep(1)
                asyncio.create_task(send_ping())

                while True:
                    response = await websocket.recv()
                    message = json.loads(response)
                    logger.info(f"Received message: {message}")
                    if message.get("action") == "AUTH":
                        auth_response = {
                            "id": message["id"],
                            "origin_action": "AUTH",
                            "result": {
                                "browser_id": device_id,
                                "user_id": user_id,
                                "user_agent": custom_headers["User-Agent"],
                                "timestamp": int(time.time()),
                                "device_type": "extension",
                                "version": "3.3.2",
                            },
                        }
                        logger.debug(f"Sending AUTH response: {auth_response}")
                        await websocket.send(json.dumps(auth_response))

                    elif message.get("action") == "PONG":
                        pong_response = {"id": message["id"], "origin_action": "PONG"}
                        logger.debug(f"Sending PONG response: {pong_response}")
                        await websocket.send(json.dumps(pong_response))

                        # Check if proxy already exists before adding to super_proxy.txt
                        with open(SUPER_PROXY_FILE, "r") as f:
                            existing_proxies = f.read().splitlines()
                        if socks5_proxy not in existing_proxies:
                            with open(SUPER_PROXY_FILE, "a") as f:
                                f.write(f"{socks5_proxy}\n")

        except Exception as e:
            logger.error(f"Error in connection: {e}")
            if "Empty connect reply" in str(e):
                await remove_proxy_from_file(PROXY_LIST_FILE, socks5_proxy[len("socks5://"):])
            else:
                logger.error(f"Removing {socks5_proxy} due to error: {e}")
                await remove_proxy_from_file(PROXY_LIST_FILE, socks5_proxy[len("socks5://"):])

async def main():
    with open(USER_ID_FILE, "r") as file:
        user_id = file.read().strip()
    with open(PROXY_LIST_FILE, "r") as file:
        socks5_proxy_list = file.read().splitlines()
    for i, proxy in enumerate(socks5_proxy_list):
        if not proxy.startswith("socks5://"):
            socks5_proxy_list[i] = 'socks5://' + proxy

    # Fetch external proxies and combine with existing ones
    external_proxies = await fetch_external_proxies()
    all_proxies = list(set(external_proxies.splitlines() + socks5_proxy_list))

    tasks = []
    for proxy in all_proxies:
        task = asyncio.create_task(connect_to_wss(proxy, user_id))
        tasks.append(task)

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
