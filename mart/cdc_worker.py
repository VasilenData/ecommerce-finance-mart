"""Consume a bounded number of real Kafka records with database-owned offsets."""

import argparse
import json
import os
import time

import duckdb

from mart.cdc import TOPIC, apply_batch, initialize, last_offset


def consume(db, *, count, bootstrap="localhost:19092", crash_before_commit=False, timeout=90):
    from confluent_kafka import Consumer, TopicPartition

    with duckdb.connect(str(db)) as con:
        initialize(con)
        consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": "finance-evidence",
                             "enable.auto.commit": False, "enable.auto.offset.store": False,
                             "auto.offset.reset": "error"})
        deadline = time.monotonic() + timeout
        received = []
        try:
            while time.monotonic() < deadline:
                topic = consumer.list_topics(timeout=10).topics.get(TOPIC)
                if topic is not None and topic.error is None:
                    if set(topic.partitions) != {0}:
                        raise ValueError("The source contract requires exactly one topic partition")
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Debezium orders topic did not become available")
            consumer.assign([TopicPartition(TOPIC, 0, last_offset(con) + 1)])
            while len(received) < count and time.monotonic() < deadline:
                msg = consumer.poll(1)
                if msg is None:
                    continue
                if msg.error():
                    raise RuntimeError(str(msg.error()))
                record = {"topic": msg.topic(), "partition": msg.partition(), "offset": msg.offset(),
                          "key": json.loads(msg.key()), "value": json.loads(msg.value()) if msg.value() else None}
                apply_batch(con, [record], before_commit=(lambda: os._exit(86)) if crash_before_commit else None)
                received.append(record)
        finally:
            consumer.close()
        if len(received) != count:
            raise TimeoutError(f"Expected {count} records, consumed {len(received)} within {timeout}s")
        return received


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--crash-before-commit", action="store_true", help="Controlled recovery exercise only; exits 86")
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("count must be positive")
    consume(args.database, count=args.count, crash_before_commit=args.crash_before_commit)
