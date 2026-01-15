import asyncio
from typing import AsyncGenerator
from loguru import logger

class LogStream:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    def sink(self, message):
        """
        Loguru sink that puts messages into the queue.
        """
        # message.record contains structured data, but message itself is the formatted string
        self.queue.put_nowait(message)

    async def event_generator(self) -> AsyncGenerator[str, None]:
        """
        Yields log messages as they come in.
        """
        while True:
            # Wait for next log message
            message = await self.queue.get()
            yield message
            self.queue.task_done()

# Global instance
log_stream = LogStream()

# Add sink to loguru
logger.add(log_stream.sink, format="{time:HH:mm:ss} | {level} | {message}")
