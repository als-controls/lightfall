"""CLI entry point for the LUCID exporter service."""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import signal
import sys


def main(argv: list[str] | None = None) -> None:
    """Run the LUCID exporter service."""
    parser = argparse.ArgumentParser(
        prog="lucid-exporter",
        description="Headless data export service for LUCID",
    )
    parser.add_argument(
        "--nats",
        default="nats://localhost:4222",
        help="NATS server URL (default: nats://localhost:4222)",
    )
    parser.add_argument(
        "--hostname",
        default=platform.node(),
        help="Hostname for topic routing (default: system hostname)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("lucid.exporter")

    from lucid.exporter.service import ExporterService

    service = ExporterService(nats_url=args.nats, hostname=args.hostname)

    async def _run() -> None:
        loop = asyncio.get_running_loop()

        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(service.stop()))

        logger.info("Starting lucid-exporter (hostname=%s, nats=%s)", args.hostname, args.nats)
        try:
            await service.run()
        except KeyboardInterrupt:
            pass
        finally:
            await service.stop()
            logger.info("Exporter stopped")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
