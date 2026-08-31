"""KafkaBatchAdapter — batch-reads messages from Kafka topics (ADR-0026 Section D).

supports_pushdown=True (topic + partition: 1 shard = 1 partition)
supports_incremental=True (offset-based)
connect() MUST only be called inside bwrap.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

try:
    from confluent_kafka import Consumer, TopicPartition  # type: ignore[import]
    KAFKA_AVAILABLE = True
except ImportError:
    Consumer = None  # type: ignore[assignment,misc]
    TopicPartition = None  # type: ignore[assignment,misc]
    KAFKA_AVAILABLE = False

from ..protocol import (
    BaseDataSourceAdapter,
    ColumnInfo,
    DataCursor,
    FilterExpr,
    PingResult,
    SecretEnv,
    SourceConfig,
    SourceQuery,
    SourceSchema,
    SourceSession,
    tcp_reachability_ping,
)


class _KafkaSession(SourceSession):
    def __init__(self, consumer: Any, topic: str) -> None:
        self.consumer = consumer
        self.topic = topic

    def close(self) -> None:
        try:
            self.consumer.close()
        except Exception:
            pass


class KafkaBatchAdapter(BaseDataSourceAdapter):
    """Batch-reads messages from a Kafka topic."""

    adapter_name = "kafka_batch"
    display_name = "Apache Kafka (Batch)"
    description = "Consume messages from a Kafka topic in batched reads."
    supported_formats = frozenset({"json", "avro"})
    locality = "any"
    network_egress = "any"
    config_schema = {
        "type": "object",
        "properties": {
                    "bootstrap_servers": {"type": "string"},
            "topic":             {"type": "string"},
            "group_id":          {"type": "string"},
        },
    }

    supports_streaming: bool = True
    supports_pushdown: bool = True   # partition-level shard pushdown
    supports_schema_discovery: bool = False  # Kafka has no schema by default
    supports_incremental: bool = True   # offset-based

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> _KafkaSession:
        if not KAFKA_AVAILABLE:
            raise ImportError("confluent-kafka is not installed.")
        bootstrap = config.raw.get(
            "bootstrap_servers",
            secret_env.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        )
        sasl_user = secret_env.get("KAFKA_SASL_USERNAME")
        sasl_pass = secret_env.get("KAFKA_SASL_PASSWORD")
        conf: dict[str, Any] = {
            "bootstrap.servers": bootstrap,
            "group.id": config.raw.get("group_id", "corvin-datasource"),
            "auto.offset.reset": config.raw.get("auto_offset_reset", "earliest"),
            "enable.auto.commit": False,
        }
        if sasl_user and sasl_pass:
            conf.update({
                "security.protocol": "SASL_SSL",
                "sasl.mechanism": "PLAIN",
                "sasl.username": sasl_user,
                "sasl.password": sasl_pass,
            })
        consumer = Consumer(conf)
        topic = config.raw.get("topic", "")
        return _KafkaSession(consumer, topic)

    def discover_schema(
        self, session: _KafkaSession, config: SourceConfig
    ) -> SourceSchema:
        # Kafka messages are untyped; return minimal schema
        return SourceSchema(
            columns=[
                ColumnInfo(name="key", dtype="string"),
                ColumnInfo(name="value", dtype="string"),
                ColumnInfo(name="partition", dtype="integer"),
                ColumnInfo(name="offset", dtype="integer"),
                ColumnInfo(name="timestamp", dtype="integer"),
            ],
            source_format="kafka",
        )

    def create_cursor(
        self,
        session: _KafkaSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        topic = session.topic
        max_messages = query.limit or config.raw.get("max_messages", 1000)
        timeout_s = float(config.raw.get("poll_timeout_s", 1.0))

        # Shard pushdown: one shard = one partition
        if query.n_shards > 1:
            partition_id = query.shard_index
            tp = TopicPartition(topic, partition_id)
            session.consumer.assign([tp])
        else:
            session.consumer.subscribe([topic])

        count = 0
        while count < max_messages:
            msg = session.consumer.poll(timeout=timeout_s)
            if msg is None:
                break
            if msg.error():
                break
            key = msg.key()
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="replace")
            value = msg.value()
            if isinstance(value, bytes):
                try:
                    import json
                    value = json.loads(value)
                except Exception:
                    value = value.decode("utf-8", errors="replace")
            row: dict = {
                "key": key,
                "value": value,
                "partition": msg.partition(),
                "offset": msg.offset(),
                "timestamp": msg.timestamp()[1] if msg.timestamp() else None,
            }
            if _passes_filters(row, query.filters):
                yield row
                count += 1

    def estimate_rows(
        self,
        session: _KafkaSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> Optional[int]:
        return None

    def close(self, session: _KafkaSession) -> None:
        session.close()

    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Credential-free reachability probe: TCP-connect to the first broker.

        ``bootstrap_servers`` is ``host:port[,host:port...]``; probes the first
        broker's host:port. Does NOT authenticate (no SASL) or fetch metadata.
        """
        raw = config.raw if config is not None else {}
        bootstrap = raw.get("bootstrap_servers", "")
        if not bootstrap:
            return PingResult(ok=False, latency_ms=0.0, detail="no bootstrap_servers configured")
        first = bootstrap.split(",")[0].strip()
        if ":" in first:
            host, _, port_str = first.rpartition(":")
            port: Any = port_str
        else:
            host, port = first, 9092
        return tcp_reachability_ping(host, port, timeout_s)


def _passes_filters(row: dict, filters: list[FilterExpr]) -> bool:
    for f in filters:
        val = row.get(f.col)
        if f.op == "=" and val != f.value:
            return False
        if f.op == "!=" and val == f.value:
            return False
    return True


__all__ = ["KafkaBatchAdapter", "KAFKA_AVAILABLE"]
