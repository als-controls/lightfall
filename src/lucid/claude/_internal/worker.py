"""QThread worker for running async Claude Agent SDK operations."""

import asyncio
import threading
from typing import Any
from queue import Queue
from PySide6.QtCore import QThread, Signal, QObject


class ClaudeWorker(QThread):
    """
    QThread worker that runs Claude Agent SDK async operations.

    This worker creates an asyncio event loop and runs Claude queries
    in a background thread, emitting signals back to the main thread.
    """

    # Signals
    message_received = Signal(str)  # Text message from assistant
    thinking_received = Signal(str)  # Thinking block from assistant
    tool_called = Signal(str, dict)  # Tool name, tool input
    tool_result = Signal(str, dict)  # Tool name, tool result
    error_occurred = Signal(str)  # Error message
    query_completed = Signal()  # Query finished successfully
    result_received = Signal(dict)  # ResultMessage with usage/cost info

    def __init__(self, client: Any, prompt: str, parent: QObject | None = None):
        """
        Initialize the worker.

        Args:
            client: ClaudeSDKClient instance (already connected)
            prompt: The prompt to send to Claude
            parent: Parent QObject
        """
        super().__init__(parent)
        self.client = client
        self.prompt = prompt
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_stop = False

    def run(self) -> None:
        """
        Thread entry point. Creates an asyncio event loop and runs the query.
        """
        try:
            # Create new event loop for this thread
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # Run the query
            self._loop.run_until_complete(self._run_query())

        except Exception as e:
            self.error_occurred.emit(f"Worker error: {str(e)}")

        finally:
            if self._loop:
                self._loop.close()

    async def _run_query(self) -> None:
        """
        Run the Claude query and process responses.
        """
        try:
            # Import here to avoid circular imports
            from claude_agent_sdk.types import (
                AssistantMessage,
                TextBlock,
                ThinkingBlock,
                ToolUseBlock,
                ToolResultBlock,
                ResultMessage,
            )

            # Send the query
            await self.client.query(self.prompt)

            # Receive and process responses
            async for msg in self.client.receive_response():
                if self._should_stop:
                    break

                if isinstance(msg, AssistantMessage):
                    # Process each content block in the message
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            self.message_received.emit(block.text)

                        elif isinstance(block, ThinkingBlock):
                            self.thinking_received.emit(block.thinking)

                        elif isinstance(block, ToolUseBlock):
                            self.tool_called.emit(block.name, block.input)

                        elif isinstance(block, ToolResultBlock):
                            self.tool_result.emit(
                                block.tool_use_id,
                                {"content": block.content, "is_error": block.is_error}
                            )

                elif isinstance(msg, ResultMessage):
                    # Query completed
                    self.result_received.emit({
                        "total_cost_usd": msg.total_cost_usd if hasattr(msg, 'total_cost_usd') else 0,
                        "input_tokens": msg.usage.get("input_tokens", 0) if msg.usage else 0,
                        "output_tokens": msg.usage.get("output_tokens", 0) if msg.usage else 0,
                    })
                    self.query_completed.emit()
                    break

        except Exception as e:
            self.error_occurred.emit(f"Query error: {str(e)}")

    def stop(self) -> None:
        """
        Request the worker to stop processing.
        """
        self._should_stop = True
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


class AgentConnectionWorker(QThread):
    """
    Worker for establishing the initial connection to Claude Agent SDK.

    This is separate from ClaudeWorker because connection happens once,
    while queries can happen multiple times.
    """

    connected = Signal(object)  # Emits the connected client
    error_occurred = Signal(str)  # Connection error

    def __init__(self, client: Any, initial_prompt: str | None = None, parent: QObject | None = None):
        """
        Initialize the connection worker.

        Args:
            client: ClaudeSDKClient instance (not yet connected)
            initial_prompt: Optional initial prompt to send on connection
            parent: Parent QObject
        """
        super().__init__(parent)
        self.client = client
        self.initial_prompt = initial_prompt

    def run(self) -> None:
        """
        Connect to Claude Agent SDK.
        """
        loop = None
        try:
            # Create event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Connect
            loop.run_until_complete(self._connect())

            self.connected.emit(self.client)

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.error_occurred.emit(f"Connection error: {str(e)}\n{error_details}")

        finally:
            if loop:
                loop.close()

    async def _connect(self) -> None:
        """
        Async connection method.
        """
        await self.client.connect(prompt=self.initial_prompt)


class PersistentClaudeWorker(QThread):
    """
    Persistent worker that maintains an event loop and connection to Claude.

    This worker stays alive for the lifetime of the agent, keeping the
    Claude CLI subprocess and event loop running. It accepts query requests
    via a queue and processes them sequentially.
    """

    # Signals
    message_received = Signal(str)
    thinking_received = Signal(str)
    tool_called = Signal(str, dict)
    tool_result = Signal(str, dict)
    error_occurred = Signal(str)
    query_completed = Signal()
    query_cancelled = Signal()  # Emitted when a query is cancelled
    result_received = Signal(dict)
    connected = Signal()

    def __init__(self, client: Any, initial_prompt: str | None = None, parent: QObject | None = None):
        """
        Initialize the persistent worker.

        Args:
            client: ClaudeSDKClient instance (not yet connected)
            initial_prompt: Optional initial prompt to send on connection
            parent: Parent QObject
        """
        super().__init__(parent)
        self.client = client
        self.initial_prompt = initial_prompt
        self._query_queue: Queue = Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_stop = False
        self._is_connected = False
        self._is_processing = False  # True when actively processing a query
        self._cancel_requested = False  # True when cancel is requested
        self._shutdown_event: asyncio.Event | None = None  # Created in run()
        self._processing_stopped = threading.Event()  # Signals when loop exits

    def run(self) -> None:
        """
        Thread entry point. Creates event loop, connects, and processes queries.
        """
        try:
            # Create persistent event loop
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._shutdown_event = asyncio.Event()

            # Connect to Claude
            print("[DEBUG] PersistentClaudeWorker: Attempting to connect...")
            self._loop.run_until_complete(self._connect())
            self._is_connected = True
            print("[DEBUG] PersistentClaudeWorker: Connected successfully!")
            self.connected.emit()

            # Process queries until stopped
            print("[DEBUG] PersistentClaudeWorker: Starting query processing loop...")
            self._loop.run_until_complete(self._process_queries())

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"[DEBUG] PersistentClaudeWorker ERROR: {error_details}")
            self.error_occurred.emit(f"Worker error: {str(e)}\n{error_details}")

        finally:
            # Signal shutdown and wait for processing loop to exit
            self._should_stop = True
            if self._processing_stopped:
                self._processing_stopped.wait(timeout=1.0)  # Wait up to 1 second
            if self._loop:
                # Cancel any pending tasks
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()
                self._loop.close()
            print("[DEBUG] PersistentClaudeWorker: Stopped")

    async def _connect(self) -> None:
        """Connect to Claude Agent SDK."""
        await self.client.connect(prompt=self.initial_prompt)

    async def _process_queries(self) -> None:
        """Process queries from the queue."""
        from queue import Empty

        try:
            while not self._should_stop:
                # Check for new queries (non-blocking with timeout)
                try:
                    # Use wait_for with timeout to allow clean exit
                    prompt = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, lambda: self._query_queue.get(timeout=0.1)
                        ),
                        timeout=0.2
                    )

                    # Process the query
                    print(f"[DEBUG] Processing query: {prompt[:50]}...")
                    await self._run_query(prompt)

                except asyncio.TimeoutError:
                    continue
                except Empty:
                    # Queue timeout or empty - continue loop
                    await asyncio.sleep(0.1)
                except RuntimeError as e:
                    # Handle "cannot schedule new futures after shutdown"
                    if "shutdown" in str(e):
                        break
                    print(f"[DEBUG] Error in query processing loop: {e}")
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"[DEBUG] Error in query processing loop: {e}")
                    await asyncio.sleep(0.1)
        finally:
            self._processing_stopped.set()

    async def _run_query(self, prompt: str) -> None:
        """
        Run a Claude query and process responses.

        Args:
            prompt: The query to send
        """
        self._is_processing = True
        self._cancel_requested = False

        try:
            from claude_agent_sdk.types import (
                AssistantMessage,
                TextBlock,
                ThinkingBlock,
                ToolUseBlock,
                ToolResultBlock,
                ResultMessage,
            )

            # Send the query
            await self.client.query(prompt)

            # Receive and process responses
            async for msg in self.client.receive_response():
                # Check for stop or cancel
                if self._should_stop or self._cancel_requested:
                    if self._cancel_requested:
                        # Drain remaining responses so the stream is clean
                        # for the next query. Silently consume without emitting.
                        await self._drain_response_stream()
                        self.query_cancelled.emit()
                    break

                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        # Check cancel between blocks too
                        if self._cancel_requested:
                            await self._drain_response_stream()
                            self.query_cancelled.emit()
                            return

                        if isinstance(block, TextBlock):
                            self.message_received.emit(block.text)
                        elif isinstance(block, ThinkingBlock):
                            self.thinking_received.emit(block.thinking)
                        elif isinstance(block, ToolUseBlock):
                            self.tool_called.emit(block.name, block.input)
                        elif isinstance(block, ToolResultBlock):
                            self.tool_result.emit(
                                block.tool_use_id,
                                {"content": block.content, "is_error": block.is_error}
                            )

                elif isinstance(msg, ResultMessage):
                    self.result_received.emit({
                        "total_cost_usd": msg.total_cost_usd if hasattr(msg, 'total_cost_usd') else 0,
                        "input_tokens": msg.usage.get("input_tokens", 0) if msg.usage else 0,
                        "output_tokens": msg.usage.get("output_tokens", 0) if msg.usage else 0,
                    })
                    self.query_completed.emit()
                    break

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            error_msg = str(e)

            # Detect CLI process death (common on Windows)
            if "stream closed" in error_msg.lower() or "0xC0000005" in error_msg:
                self.error_occurred.emit(
                    "Claude CLI process terminated unexpectedly. "
                    "This may be a transient issue — try sending another message."
                )
            else:
                self.error_occurred.emit(f"Query error: {error_msg}\n{error_details}")

        finally:
            self._is_processing = False
            self._cancel_requested = False

    async def _drain_response_stream(self) -> None:
        """Drain remaining responses from the CLI after cancellation.

        Consumes all pending messages without emitting signals, so the
        stream is clean for the next query. Uses a timeout to avoid
        hanging if the CLI is stuck.
        """
        try:
            from claude_agent_sdk.types import ResultMessage

            async for msg in self.client.receive_response():
                # Stop draining on ResultMessage (end of turn) or hard stop
                if isinstance(msg, ResultMessage) or self._should_stop:
                    break
        except Exception:
            pass  # Best-effort drain — don't crash on errors

    def send_query(self, prompt: str) -> None:
        """
        Queue a query to be processed.

        Args:
            prompt: The query to send
        """
        if self._is_connected:
            print(f"[DEBUG] Queuing query: {prompt[:50]}...")
            self._query_queue.put(prompt)
        else:
            print("[DEBUG] Cannot send query - not connected!")

    def cancel_current_query(self) -> bool:
        """
        Request cancellation of the current query.

        This sets a flag that will be checked during response processing.
        The actual cancellation happens at the next check point (between
        messages or content blocks).

        Returns:
            True if a query was being processed and cancel was requested.
        """
        if self._is_processing:
            self._cancel_requested = True
            return True
        return False

    @property
    def is_processing(self) -> bool:
        """Check if a query is currently being processed."""
        return self._is_processing

    def stop(self) -> None:
        """Stop the worker and close the connection."""
        self._should_stop = True
        self._cancel_requested = True  # Also cancel any current query
        # Signal the shutdown event if we have a loop
        if self._loop and self._shutdown_event:
            try:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
            except RuntimeError:
                pass  # Loop already closed
