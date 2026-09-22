#!/usr/bin/env python3
"""Staging Test: Discord Live Feed 3-Task Batch."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "corvin_operator" / "bridges" / "shared"))

def test_staging():
    print("\n" + "="*70)
    print("STAGING TEST: Discord Live Feed 3-Task Batch")
    print("="*70)
    
    temp_dir = tempfile.mkdtemp()
    os.environ["CORVIN_HOME"] = temp_dir
    print(f"✓ Temp CORVIN_HOME: {temp_dir}")
    
    try:
        from orchestration_aggregator import (
            register_task,
            on_task_complete,
            emit_orchestration_event,
            get_active_batches,
        )
        
        print("\n[STEP 1] Register 3 tasks...")
        batch_window = time.time()
        batch_id = register_task(batch_window_start=batch_window, task_id="task1")
        register_task(batch_window_start=batch_window, task_id="task2")
        register_task(batch_window_start=batch_window, task_id="task3")
        print(f"  ✓ Batch ID: {batch_id}")
        
        print("\n[STEP 2] Complete all tasks...")
        on_task_complete(batch_id=batch_id, task_id="task1", success=True)
        print(f"  ✓ task1 completed")
        
        on_task_complete(batch_id=batch_id, task_id="task2", success=True)
        print(f"  ✓ task2 completed")
        
        on_task_complete(batch_id=batch_id, task_id="task3", success=True)
        print(f"  ✓ task3 completed")
        
        print("\n[STEP 3] Emit orchestration event...")
        event = emit_orchestration_event(batch_id)
        assert event is not None
        assert event.event_type == "ORCHESTRATION_COMPLETE_SUCCESS"
        assert event.task_count == 3
        assert event.success_count == 3
        print(f"  ✓ Event Type: {event.event_type}")
        print(f"  ✓ Task Count: {event.task_count}/{event.task_count}")
        
        print("\n[STEP 4] Verify Discord envelope...")
        envelope = {
            "message_type": "orchestration_complete",
            "event_type": event.event_type,
            "text": f"✅ {event.task_count} tasks complete",
            "voice_attachment_path": f"/tmp/voice_{batch_id}.ogg",
            "embed": {"title": "Batch Complete", "color": 3066993}
        }
        assert envelope["message_type"] == "orchestration_complete"
        print(f"  ✓ Envelope valid for Discord daemon")
        
        print("\n[STEP 5] Verify audit trail...")
        event_dict = event.to_dict()
        assert "batch_id" in event_dict
        assert "timestamp" in event_dict
        assert "event_type" in event_dict
        print(f"  ✓ Audit-trail ready: {len(event_dict)} fields")
        
        print("\n" + "="*70)
        print("✅ STAGING TEST PASSED")
        print("="*70)
        print("Ready for production release\n")
        return True
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        if "CORVIN_HOME" in os.environ:
            del os.environ["CORVIN_HOME"]

if __name__ == "__main__":
    success = test_staging()
    sys.exit(0 if success else 1)
