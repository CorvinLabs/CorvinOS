# CorvinOS Plugin Extension Points
## Where & How to Extend the System

**Date:** 2026-07-26  
**Audience:** Plugin Developers, Enterprise Engineers

---

## Philosophy

**Tier-0 (Compliance):** Closed. No extension points. Hardcoded.  
**Tier-1 (License Infrastructure):** Extension points only. Core logic immutable.  
**Tier-2/3 (Features):** Fully configurable + extensible.

**Rule:** If you need to *replace* core logic, you can't. If you need to *extend* it, you can.

---

## Tier-1: A2A Orchestration Extension Points

### Extension Point: Custom Routing
```python
# File: corvin_plugins/a2a/routing_hooks.py

from corvin_plugins import CorvinPlugin, HookRegistry

class CustomRoutingPlugin(CorvinPlugin):
    plugin_id = "custom-routing-geo/1.0.0"
    plugin_type = "tier-2"  # Our custom plugin
    
    def on_load(self, ctx):
        a2a = ctx.registry.registry["a2a-orchestration/1.0.0"]
        
        # Register custom routing logic
        a2a.register_hook(
            "routing.select_target",
            self.geo_aware_routing,
            priority=10  # Higher priority = called first
        )
        
        ctx.logger.info("Custom geo-aware routing installed")
    
    def geo_aware_routing(self, envelope: TaskEnvelope) -> Instance | None:
        """
        Choose target instance based on geography.
        
        Return value:
          - Instance object: use this target
          - None: fallback to default routing
        """
        user_region = envelope.metadata.get("user_region", "unknown")
        
        if user_region == "eu":
            # Try EU instance first
            eu_instance = self._get_eu_instance()
            if eu_instance.is_reachable():
                return eu_instance
        
        elif user_region == "ap":
            ap_instance = self._get_ap_instance()
            if ap_instance.is_reachable():
                return ap_instance
        
        # Fallback: let A2A choose default
        return None
    
    def _get_eu_instance(self) -> Instance:
        """Fetch EU instance from config."""
        config = self.get_config()
        return Instance(id=config["eu_instance_id"], region="eu")
```

### Extension Point: Additional Attestation Checks
```python
class EnhancedAttestationPlugin(CorvinPlugin):
    plugin_id = "enhanced-attestation/1.0.0"
    plugin_type = "tier-2"
    
    def on_load(self, ctx):
        a2a = ctx.registry.registry["a2a-orchestration/1.0.0"]
        
        # ADD custom attestation checks
        # Note: core Ed25519 check always runs first (immutable)
        a2a.register_hook(
            "attestation.custom_verify",
            self.check_ip_whitelist
        )
    
    def check_ip_whitelist(self, instance: Instance) -> bool:
        """
        ADDITIONAL check: is instance IP whitelisted?
        
        Core Ed25519 check always runs. This is ADD-ON.
        If this returns False, attestation FAILS.
        """
        config = self.get_config()
        whitelist = config.get("allowed_ips", [])
        
        if instance.ip not in whitelist:
            return False  # Fail attestation
        
        return True  # Pass this check
```

### Extension Point: Pre/Post Send Hooks
```python
class A2AMonitoringPlugin(CorvinPlugin):
    plugin_id = "a2a-monitoring/1.0.0"
    
    def on_load(self, ctx):
        a2a = ctx.registry.registry["a2a-orchestration/1.0.0"]
        
        a2a.register_hook("envelope.pre_send", self.before_send)
        a2a.register_hook("envelope.post_send", self.after_send)
    
    def before_send(self, envelope: TaskEnvelope) -> TaskEnvelope | None:
        """
        Called before A2A sends envelope.
        Can inspect, log, or block.
        
        Return:
          - TaskEnvelope: continue with (possibly modified) envelope
          - None: block sending this envelope
        """
        # Log to monitoring system
        self.monitor.record_a2a_send(
            source=os.getenv("CORVIN_INSTANCE_ID"),
            target=envelope.target_instance.id,
            envelope_id=envelope.envelope_id
        )
        
        return envelope  # Continue
    
    def after_send(self, result: SendResult) -> None:
        """
        Called after send completes.
        Record metrics, handle errors, etc.
        """
        self.monitor.record_a2a_result(
            envelope_id=result.envelope_id,
            success=result.ok,
            latency_ms=result.latency_ms,
            error=result.error if not result.ok else None
        )
```

### What You CANNOT Do
```python
# ❌ Replace core Ed25519 verification
a2a.register_hook("attestation.verify", my_custom_verify)
# → This will raise Error: "attestation.verify is immutable"

# ❌ Silence audit events
a2a.disable_audit_logging()
# → No such method exists

# ❌ Access raw peer instance list
a2a.get_all_peers()
# → Returns empty list; only A2A internals have this
```

---

## Tier-1: TDE Routing Extension Points

### Extension Point: Custom Cost Model
```python
from corvin_plugins import CorvinPlugin

class CustomCostModelPlugin(CorvinPlugin):
    plugin_id = "custom-cost-model/1.0.0"
    
    def on_load(self, ctx):
        tde = ctx.registry.registry["tde-routing/1.0.0"]
        
        tde.register_cost_model(
            "internal-pricing",
            self.calculate_cost
        )
    
    def calculate_cost(self, request: Request) -> CostEstimate:
        """
        Calculate token cost for this request.
        Called by TDE before routing decision.
        
        Args:
          request: The user's request (prompt + context)
        
        Returns:
          CostEstimate(
            model_name: str,
            tokens_in: int,
            tokens_out_estimated: int,
            cost_usd: float
          )
        """
        config = self.get_config()
        internal_rates = config["rates"]  # {"claude-sonnet": 0.003, ...}
        
        # Tokenize request
        tokens = self.tokenizer.count_tokens(request.prompt)
        
        # Estimate output (50% of input is common)
        estimated_output = int(tokens * 0.5)
        
        # Apply internal pricing
        rate = internal_rates.get(config["preferred_model"], 0.003)
        cost = (tokens + estimated_output) * rate / 1_000_000
        
        return CostEstimate(
            model_name=config["preferred_model"],
            tokens_in=tokens,
            tokens_out_estimated=estimated_output,
            cost_usd=cost
        )
```

### Extension Point: Custom Routing Strategy
```python
class RegionalRoutingPlugin(CorvinPlugin):
    plugin_id = "regional-routing/1.0.0"
    
    def on_load(self, ctx):
        tde = ctx.registry.registry["tde-routing/1.0.0"]
        
        tde.register_router_strategy(
            "regional-low-latency",
            self.route_by_region
        )
    
    def route_by_region(self, request: Request, engines: list[Engine]) -> Engine:
        """
        Route request based on user's region.
        
        Args:
          request: User's request (has metadata.user_region)
          engines: Available engines (Haiku, Sonnet, Opus, etc.)
        
        Returns:
          Selected engine
        """
        user_region = request.metadata.get("user_region", "us")
        
        # Regional preferences
        region_preferences = {
            "eu": ["claude-sonnet", "claude-opus"],
            "ap": ["claude-opus"],
            "us": ["claude-opus", "claude-sonnet"],
        }
        
        for model in region_preferences.get(user_region, []):
            engine = next(
                (e for e in engines if e.model == model),
                None
            )
            if engine:
                return engine
        
        return engines[0]  # Fallback to first available
```

### What You CANNOT Do
```python
# ❌ Override token accounting
tde.override_token_counter(my_counter)
# → TDE always uses its own counter

# ❌ Bypass budget enforcement
tde.disable_budget_gate()
# → No such method

# ❌ Access internal routing state
tde.get_all_routing_decisions()
# → Returns empty; only metrics are exposed
```

---

## Tier-1: Conversation Recall Extension Points

### Extension Point: Custom Storage Backend
```python
from corvin_plugins import CorvinPlugin, RecallBackend

class PostgresRecallPlugin(CorvinPlugin):
    plugin_id = "postgres-recall-backend/1.0.0"
    
    def on_load(self, ctx):
        recall = ctx.registry.registry["conversation-recall/1.0.0"]
        
        # Register custom storage backend
        pg_backend = PostgresBackend(
            connection_string=self.get_config()["db_url"]
        )
        
        recall.register_storage_backend("postgres", pg_backend)
        ctx.logger.info("Postgres backend registered")

class PostgresBackend(RecallBackend):
    """Custom storage backend for recall."""
    
    def __init__(self, connection_string: str):
        self.pool = ConnectionPool(connection_string)
    
    def store_conversation(
        self,
        user_id: str,
        conversation_id: str,
        turns: list[Turn]
    ) -> Result:
        """
        Store conversation turns in Postgres.
        
        Schema: (immutable, defined by Recall core)
          - user_id (hashed, PII-protected)
          - conversation_id
          - turn_index
          - role (user | assistant)
          - content (encrypted)
          - timestamp
          - metadata (model, tokens, cost, etc.)
        """
        try:
            with self.pool.connection() as conn:
                for turn in turns:
                    conn.execute(
                        """
                        INSERT INTO recall_turns
                        (user_id, conversation_id, turn_index, role, content, timestamp)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            conversation_id,
                            turn.index,
                            turn.role,
                            turn.encrypted_content,  # Recall core handles encryption
                            turn.timestamp
                        )
                    )
                conn.commit()
            
            return Result(ok=True, message="Stored in Postgres")
        
        except Exception as e:
            return Result(ok=False, message=f"Postgres error: {e}")
    
    def retrieve_conversation(
        self,
        user_id: str,
        conversation_id: str
    ) -> Result[list[Turn]]:
        """
        Retrieve conversation from Postgres.
        Core Recall will decrypt content.
        """
        try:
            with self.pool.connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM recall_turns
                    WHERE user_id = %s AND conversation_id = %s
                    ORDER BY turn_index ASC
                    """,
                    (user_id, conversation_id)
                ).fetchall()
            
            turns = [Turn.from_row(row) for row in rows]
            return Result(ok=True, data=turns)
        
        except Exception as e:
            return Result(ok=False, message=f"Postgres error: {e}")
    
    def delete_conversation(
        self,
        user_id: str,
        conversation_id: str
    ) -> Result:
        """
        Delete conversation (GDPR erasure).
        Called by Recall core's erasure handler.
        """
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    """
                    DELETE FROM recall_turns
                    WHERE user_id = %s AND conversation_id = %s
                    """,
                    (user_id, conversation_id)
                )
                conn.commit()
            
            return Result(ok=True, message="Deleted from Postgres")
        
        except Exception as e:
            return Result(ok=False, message=f"Postgres error: {e}")
```

### Extension Point: Pre/Post Storage Hooks
```python
class RecallMonitoringPlugin(CorvinPlugin):
    plugin_id = "recall-monitoring/1.0.0"
    
    def on_load(self, ctx):
        recall = ctx.registry.registry["conversation-recall/1.0.0"]
        
        recall.register_hook("storage.pre_store", self.before_store)
        recall.register_hook("storage.post_retrieve", self.after_retrieve)
    
    def before_store(self, turn: Turn) -> Turn | None:
        """
        Called before turn is stored.
        Can inspect, log, or block.
        """
        # Log to analytics
        self.analytics.record_turn_stored(
            conversation_id=turn.conversation_id,
            role=turn.role,
            tokens=turn.token_count
        )
        
        return turn  # Continue
    
    def after_retrieve(self, turns: list[Turn]) -> list[Turn]:
        """
        Called after turns retrieved.
        Can augment, filter, etc.
        """
        # Log retrieval
        self.analytics.record_conversation_loaded(
            conversation_id=turns[0].conversation_id if turns else None,
            turn_count=len(turns)
        )
        
        return turns  # Continue
```

### What You CANNOT Do
```python
# ❌ Access raw encrypted content
backend.get_raw_encrypted_content(user_id)
# → Returns decrypted only (Recall core handles keys)

# ❌ Modify core encryption key
recall.set_encryption_key(my_key)
# → No such method (system-managed)

# ❌ Disable retention policy
recall.disable_retention()
# → No such method (GDPR requirement)
```

---

## Tier-2/3: Standard Features (Fully Extensible)

Tier-2/3 plugins can have custom extension points too. Example:

### Forge Extension: Custom Sandbox Providers
```python
class CustomSandboxPlugin(CorvinPlugin):
    plugin_id = "custom-sandbox/1.0.0"
    
    def on_load(self, ctx):
        forge = ctx.registry.registry["forge/1.0.0"]
        
        forge.register_sandbox_provider(
            "kubernetes",
            KubernetesSandboxProvider(...)
        )
```

---

## Extension Point Registry (Discovery)

**How to see what hooks are available:**

```python
# Programmatic
a2a = registry.registry["a2a-orchestration/1.0.0"]
available_hooks = a2a.get_available_hooks()
# Returns:
# [
#   {"name": "routing.select_target", "immutable": False, "priority_available": True},
#   {"name": "envelope.pre_send", "immutable": False},
#   {"name": "attestation.verify", "immutable": True},  # ← Can't extend
# ]

# CLI
corvinctl plugin hooks a2a-orchestration/1.0.0
# Lists all extension points with docs
```

---

## Testing Extension Points

### Unit Test: Custom Routing Hook
```python
def test_custom_routing_hook():
    """Verify custom routing is called."""
    registry = PluginRegistry()
    a2a = registry.registry["a2a-orchestration/1.0.0"]
    
    # Register mock hook
    called = []
    def mock_routing(envelope):
        called.append(envelope)
        return None  # Fallback to default
    
    a2a.register_hook("routing.select_target", mock_routing)
    
    # Send envelope
    envelope = TaskEnvelope(...)
    a2a.send_task(envelope)
    
    # Verify hook was called
    assert len(called) == 1
    assert called[0].envelope_id == envelope.envelope_id
```

### Integration Test: Custom Storage Backend
```python
@pytest.mark.asyncio
async def test_postgres_backend_integration():
    """Verify Postgres backend stores + retrieves."""
    backend = PostgresBackend("postgresql://localhost/test")
    
    # Store conversation
    turn = Turn(
        user_id="user-123",
        conversation_id="conv-456",
        role="user",
        content="Hello"
    )
    
    result = backend.store_conversation("user-123", "conv-456", [turn])
    assert result.ok
    
    # Retrieve
    result = backend.retrieve_conversation("user-123", "conv-456")
    assert result.ok
    assert len(result.data) == 1
    assert result.data[0].content == "Hello"
```

---

## Examples by Use Case

### Use Case 1: Geographic Routing
**Extend:** A2A routing  
**Plugin:** `geo-routing/1.0.0`  
**Hook:** `routing.select_target`

```python
def route_by_geo(envelope):
    user_geo = envelope.metadata["user_country"]
    if user_geo in eu_countries:
        return eu_instance
    return us_instance
```

### Use Case 2: Cost Optimization
**Extend:** TDE  
**Plugin:** `cost-optimizer/1.0.0`  
**Hook:** `cost_model`

```python
def my_cost_model(request):
    return CostEstimate(
        model="claude-haiku",  # Always use cheaper model
        cost_usd=0.0001
    )
```

### Use Case 3: Custom Data Storage
**Extend:** Recall  
**Plugin:** `dynamodb-recall/1.0.0`  
**Hook:** `storage_backend`

```python
class DynamoDBBackend(RecallBackend):
    def store_conversation(self, user_id, conv_id, turns):
        table = dynamodb.Table("recall-turns")
        for turn in turns:
            table.put_item(Item={...})
```

---

## Do's and Don'ts

### ✅ DO
- Register hooks during `on_load()`
- Return proper types (or None for default)
- Audit your extensions
- Test both success and failure paths
- Document your hooks in plugin docs

### ❌ DON'T
- Call non-public methods (those starting with `_`)
- Store state outside your plugin context
- Block indefinitely (use timeouts)
- Log PII (use hashing/redaction)
- Assume hook ordering (register priority, handle all orders)

---

## API Reference: Hook Registry

```python
# Register a hook
plugin.register_hook(
    hook_name: str,           # e.g., "routing.select_target"
    handler: Callable,        # Function to call
    priority: int = 0         # Higher = called first
) -> Result

# List available hooks
plugin.get_available_hooks() -> list[HookInfo]

# Unregister a hook
plugin.unregister_hook(hook_name: str) -> Result
```

---

## Summary

**Tier-1 plugins (A2A, TDE, Recall) have strategic extension points:**
- Custom routing/cost/storage logic
- Pre/post hooks for monitoring
- **But:** Core IP (attestation, token counting, encryption) is immutable

**Tier-2/3 plugins are fully extensible.**

**Admin can register extension hooks without redeploying.**

This keeps business IP protected while allowing customization.

