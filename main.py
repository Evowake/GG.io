import asyncio
import ssl
import json
import time
import uuid
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
import aiohttp
import websockets
from fake_useragent import UserAgent
import subprocess

async def install_dependencies():
    try:
        subprocess.run(["pip", "install", "-r", "requirements.txt"], check=True)
        logger.info("Dependencies installed successfully.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error installing dependencies: {e}")
        raise

# Configuration
WSS_URI = "wss://proxy.wynd.network:4650/"
SERVER_HOSTNAME = "proxy.wynd.network"
PROXY_LIST_FILE = "proxy_list.txt"
SUPER_PROXY_FILE = "super_proxy.txt"
USER_ID_FILE = "user_id.txt"
URLS_FILE = "urls.txt" # File containing URLs

# Hardcoded User Agent String
user_agent_string = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

# Toggle for using fake user agent
use_fake_user_agent = True

# Initialize UserAgent for random user agent generation
user_agent = UserAgent()

# Lock for asynchronous file operations
lock = asyncio.Lock()

# Function to read URLs from a file
async def read_urls_from_file(file_path):
    async with lock:
        with open(file_path, "r") as file:
            urls = file.read().splitlines()
    return urls

# Function to fetch external proxies from multiple URLs
async def fetch_external_proxies(urls):
    proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [session.get(url) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for response in responses:
            if isinstance(response, Exception):
                logger.warning(f"Failed to fetch proxies: {response}")
                continue
            if response.status == 200:
                response_text = await response.text()
                proxies.extend(response_text.splitlines())
            else:
                logger.warning(f"Failed to fetch proxies from {url}: {response.status}")
    return proxies

# Function to remove a proxy from a file
async def remove_proxy_from_file(file_path, proxy):
    async with lock:
        logger.info(f"Removing {proxy} from {file_path}")
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

# Function to check if a proxy is ignored
async def is_proxy_ignored(proxy):
    async with lock:
        try:
            with open("ignored_proxies.txt", "r") as file:
                ignored_proxies = file.read().splitlines()
            return proxy.lower() in [line.lower() for line in ignored_proxies]
        except FileNotFoundError:
            return False

# Function to add a proxy to the ignore list
async def add_proxy_to_ignore_list(proxy):
    async with lock:
        try:
            with open("ignored_proxies.txt", "a") as file:
                file.write(f"{proxy}\n")
            logger.info(f"Added {proxy} to ignore list")
        except Exception as e:
            logger.error(f"Error adding {proxy} to ignore list: {e}")

# Function to connect to WSS
async def connect_to_wss(socks5_proxy, user_id):
    if await is_proxy_ignored(socks5_proxy):
        logger.info(f"Skipping ignored proxy: {socks5_proxy}")
        return

    if not socks5_proxy.startswith("socks5://"):
        socks5_proxy = 'socks5://' + socks5_proxy

    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, socks5_proxy))
    logger.info(f"Device ID: {device_id}")

    try:
        if use_fake_user_agent:
            custom_headers = {"User-Agent": user_agent.random}
        else:
            custom_headers = {"User-Agent": user_agent_string}

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

                with open(SUPER_PROXY_FILE, "r") as f:
                    existing_proxies = f.read().splitlines()
                proxy_without_scheme = socks5_proxy[len("socks5://"):]
                if proxy_without_scheme not in existing_proxies:
                    with open(SUPER_PROXY_FILE, "a") as f:
                        f.write(f"{proxy_without_scheme}\n")

    except Exception as e:
        logger.error(f"Error in connection: {e}")
        # Strip the socks5:// prefix before adding to the ignore list
        proxy_without_scheme = socks5_proxy[len("socks5://"):]
        await add_proxy_to_ignore_list(proxy_without_scheme)
        if "Empty connect reply" in str(e):
            await remove_proxy_from_file(PROXY_LIST_FILE, proxy_without_scheme)
        else:
            logger.error(f"Removing {socks5_proxy} due to error: {e}")
            await remove_proxy_from_file(PROXY_LIST_FILE, proxy_without_scheme)



# Main function
async def main():
    await install_dependencies() # Install dependencies before proceeding with main functionality
    
    try:
        with open(USER_ID_FILE, "r") as file:
            user_id = file.read().strip()

        with open(PROXY_LIST_FILE, "r") as file:
            socks5_proxy_list = file.read().splitlines()

        urls = await read_urls_from_file(URLS_FILE)
        external_proxies = await fetch_external_proxies(urls)
        all_proxies = list(set(external_proxies + socks5_proxy_list))
        all_proxies = [proxy for proxy in all_proxies if not await is_proxy_ignored(proxy)]

        tasks = [connect_to_wss(proxy, user_id) for proxy in all_proxies]
        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        logger.info("Task was cancelled")
    except KeyboardInterrupt:
        logger.info("Program was interrupted by the user")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program was interrupted by the user")
