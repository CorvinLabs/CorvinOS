"""Server-Sent Events (SSE) for real-time sync status updates.

Clients connect to /api/console/github/events and receive live updates:
- sync_started
- sync_completed
- sync_failed
- status_updated
"""

from flask import Blueprint, Response, request
from functools import wraps
import json
from datetime import datetime
import logging
from core.endpoints.k1_decorators import k1_flask

logger = logging.getLogger(__name__)

sse_bp = Blueprint('github_sse', __name__, url_prefix='/api/console/github')

# Track connected clients
_clients = []
_lock = __import__('threading').Lock()


def sse_event(event_type: str, data: dict) -> str:
    """Format SSE event."""
    return f"""data: {json.dumps({
        'event': event_type,
        'timestamp': datetime.utcnow().isoformat(),
        'data': data
    })}

"""


@sse_bp.route('/events', methods=['GET'])
@k1_flask()
def sync_events():
    """
    Server-Sent Events stream for sync status updates.

    GET /api/console/github/events

    Returns: text/event-stream with live events
    """

    def generate():
        """Generate SSE stream."""
        # Send initial connection event
        yield sse_event('connected', {'message': 'Listening to sync events'})

        # Get sync worker and subscribe
        from .github_integration import get_sync_worker

        worker = get_sync_worker()

        event_queue = []

        def on_sync_event(payload):
            """Callback when sync event occurs."""
            event_queue.append(payload)

        # Subscribe to worker events
        worker.subscribe(on_sync_event)

        try:
            while True:
                # Send any queued events
                while event_queue:
                    event = event_queue.pop(0)
                    yield sse_event(
                        event.get('event'),
                        {
                            'details': event.get('details'),
                            'timestamp': event.get('timestamp'),
                        }
                    )

                # Check for client disconnect (request closed)
                if request.environ.get('wsgi.input').closed:
                    logger.info('SSE client disconnected')
                    break

                # Small delay to avoid busy-waiting
                import time
                time.sleep(0.5)

        except GeneratorExit:
            logger.info('SSE stream closed')
        finally:
            # Remove callback
            try:
                worker.callbacks.remove(on_sync_event)
            except (ValueError, AttributeError):
                pass

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@sse_bp.route('/worker/status', methods=['GET'])
@k1_flask()
def worker_status():
    """Get sync worker status."""
    from .github_integration import get_sync_worker

    worker = get_sync_worker()
    status = worker.get_status()

    return status, 200


@sse_bp.route('/worker/start', methods=['POST'])
@k1_flask()
def start_worker():
    """Start sync worker."""
    from .github_integration import start_sync_worker

    try:
        worker = start_sync_worker()
        return {
            'success': True,
            'message': 'Sync worker started',
            'status': worker.get_status(),
        }, 200
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }, 500


@sse_bp.route('/worker/stop', methods=['POST'])
@k1_flask()
def stop_worker():
    """Stop sync worker."""
    from .github_integration import get_sync_worker, stop_sync_worker

    try:
        stop_sync_worker()
        return {
            'success': True,
            'message': 'Sync worker stopped',
        }, 200
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
        }, 500
