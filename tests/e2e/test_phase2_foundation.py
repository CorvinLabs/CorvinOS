import pytest
import asyncio
from core.orchestration.subsystems.notification_daemon import (
    NotificationDaemon, NotificationEvent, EventTopic, EventPriority
)
from core.skill_forge.skill_forge_v2 import SkillForgeV2, SkillMetadata
from core.data.datahub import DataHub, datahub_set, datahub_get

@pytest.mark.asyncio
async def test_notification_daemon_emit():
    daemon = NotificationDaemon()
    await daemon.start()
    
    event = NotificationEvent(
        topic=EventTopic.SKILL_EXECUTED,
        priority=EventPriority.NORMAL,
        payload={"skill": "test"},
        source="test_suite"
    )
    
    result = await daemon.emit(event)
    assert result == True
    await daemon.stop()

@pytest.mark.asyncio
async def test_notification_daemon_subscribe():
    daemon = NotificationDaemon()
    await daemon.start()
    
    events_received = []
    
    async def handler(event):
        events_received.append(event)
    
    daemon.subscribe(EventTopic.SKILL_EXECUTED, handler)
    
    event = NotificationEvent(
        topic=EventTopic.SKILL_EXECUTED,
        priority=EventPriority.NORMAL,
        payload={"test": True},
        source="test"
    )
    
    await daemon.emit(event)
    await asyncio.sleep(0.1)  # Let handler process
    await daemon.stop()
    
    assert len(events_received) >= 1

@pytest.mark.asyncio
async def test_skill_forge_register():
    daemon = NotificationDaemon()
    await daemon.start()
    
    sf = SkillForgeV2(daemon)
    
    async def test_handler(data):
        return {"result": "ok"}
    
    metadata = SkillMetadata(
        skill_id="test.skill",
        version="1.0.0",
        category="routing",
        handler=test_handler
    )
    
    result = await sf.register_skill(metadata)
    assert result == True
    assert sf.get_skill("test.skill") == metadata
    
    await daemon.stop()

@pytest.mark.asyncio
async def test_skill_forge_execute():
    daemon = NotificationDaemon()
    await daemon.start()
    
    sf = SkillForgeV2(daemon)
    
    async def add_skill(data):
        return {"sum": data["a"] + data["b"]}
    
    metadata = SkillMetadata(
        skill_id="math.add",
        version="1.0.0",
        category="util",
        handler=add_skill
    )
    
    await sf.register_skill(metadata)
    result = await sf.execute_skill("math.add", {"a": 2, "b": 3})
    
    assert result.get("sum") == 5
    await daemon.stop()

@pytest.mark.asyncio
async def test_datahub_store_retrieve():
    hub = DataHub()
    
    await hub.store("key1", {"data": "value"}, "test_source")
    result = await hub.retrieve("key1")
    
    assert result == {"data": "value"}

@pytest.mark.asyncio
async def test_datahub_by_source():
    hub = DataHub()
    
    await hub.store("key1", "val1", "source_a")
    await hub.store("key2", "val2", "source_a")
    await hub.store("key3", "val3", "source_b")
    
    results = await hub.list_by_source("source_a")
    assert len(results) == 2
