"""k=1 Endpoint Decorators for all transports (ADR-0515 wiring)."""

import functools
import logging
from typing import Any, Callable, Dict, Optional

from core.endpoints.k1_context import K1RequestContext, get_k1_context

logger = logging.getLogger(__name__)


def k1_flask_route(transport: str = 'http'):
    """
    Decorator for Flask routes to enforce k=1 endpoint architecture.

    Usage:
        @app.route('/api/settings/<flag_id>', methods=['PUT'])
        @k1_flask_route()
        async def update_setting_flask(flag_id: str):
            ctx = get_k1_context()
            # ... use ctx.connection, ctx.session, ctx.task_queue, ctx.pipeline
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Create k=1 context for this Flask request
            ctx = await K1RequestContext.create(transport)
            logger.debug(f"[k=1 Flask] Created context for {ctx.request_id}")

            try:
                async with ctx.connection:
                    # Now run the handler within k=1 boundary
                    result = await func(*args, **kwargs)
                    return result
            except Exception as e:
                logger.exception(f"[k=1 Flask] Error in {func.__name__}: {e}")
                raise
            finally:
                # Cleanup happens automatically on context exit
                logger.debug(f"[k=1 Flask] Cleaned up context {ctx.request_id}")

        return wrapper
    return decorator


def k1_cli_command(transport: str = 'cli'):
    """
    Decorator for CLI commands to enforce k=1 endpoint architecture.

    Usage:
        @cli.command('update-setting')
        @click.option('--flag-id', required=True)
        @k1_cli_command()
        async def update_setting_cli(flag_id: str):
            ctx = get_k1_context()
            # ... use ctx.connection, ctx.session, ctx.task_queue, ctx.pipeline
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = await K1RequestContext.create(transport)
            logger.debug(f"[k=1 CLI] Created context for {ctx.request_id}")

            try:
                async with ctx.connection:
                    result = await func(*args, **kwargs)
                    return result
            except Exception as e:
                logger.exception(f"[k=1 CLI] Error in {func.__name__}: {e}")
                raise
            finally:
                logger.debug(f"[k=1 CLI] Cleaned up context {ctx.request_id}")

        return wrapper
    return decorator


def k1_async_task(transport: str = 'async'):
    """
    Decorator for async worker tasks to enforce k=1 endpoint architecture.

    Usage:
        @k1_async_task()
        async def process_background_job(job_id: str):
            ctx = get_k1_context()
            # ... use ctx.connection, ctx.session, ctx.task_queue, ctx.pipeline
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = await K1RequestContext.create(transport)
            logger.debug(f"[k=1 Async] Created context for {ctx.request_id}")

            try:
                async with ctx.connection:
                    result = await func(*args, **kwargs)
                    return result
            except Exception as e:
                logger.exception(f"[k=1 Async] Error in {func.__name__}: {e}")
                raise
            finally:
                logger.debug(f"[k=1 Async] Cleaned up context {ctx.request_id}")

        return wrapper
    return decorator


def k1_websocket(transport: str = 'ws'):
    """
    Decorator for WebSocket handlers to enforce k=1 endpoint architecture.

    Usage:
        @sockets.route('/ws/stream')
        @k1_websocket()
        async def handle_stream(ws):
            ctx = get_k1_context()
            # ... use ctx.connection, ctx.session, ctx.task_queue, ctx.pipeline
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = await K1RequestContext.create(transport)
            logger.debug(f"[k=1 WebSocket] Created context for {ctx.request_id}")

            try:
                async with ctx.connection:
                    result = await func(*args, **kwargs)
                    return result
            except Exception as e:
                logger.exception(f"[k=1 WebSocket] Error in {func.__name__}: {e}")
                raise
            finally:
                logger.debug(f"[k=1 WebSocket] Cleaned up context {ctx.request_id}")

        return wrapper
    return decorator


# Shorthand decorators (no args)
def k1_flask(*args, **kwargs):
    """Shorthand for @k1_flask_route()."""
    if args and callable(args[0]):
        # Called without arguments: @k1_flask
        func = args[0]
        return k1_flask_route()(func)
    else:
        # Called with arguments: @k1_flask(transport='...')
        return k1_flask_route(*args, **kwargs)


def k1_cli(*args, **kwargs):
    """Shorthand for @k1_cli_command()."""
    if args and callable(args[0]):
        func = args[0]
        return k1_cli_command()(func)
    else:
        return k1_cli_command(*args, **kwargs)


def k1_async(*args, **kwargs):
    """Shorthand for @k1_async_task()."""
    if args and callable(args[0]):
        func = args[0]
        return k1_async_task()(func)
    else:
        return k1_async_task(*args, **kwargs)


def k1_ws(*args, **kwargs):
    """Shorthand for @k1_websocket()."""
    if args and callable(args[0]):
        func = args[0]
        return k1_websocket()(func)
    else:
        return k1_websocket(*args, **kwargs)
