"""Runs the three lab scenarios and prints a log of what happens."""

from order_system import LockService, OrderEvent, OrderStore, StaleTokenError


def header(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def scenario_1_ordering_and_duplicates():
    header("TEST 1: Versioned ordering + duplicates  (delivery: v2, v1, v2, v3)")
    store = OrderStore()
    deliveries = [
        OrderEvent("order-1", 2, "PAID"),     # arrives early
        OrderEvent("order-1", 1, "CREATED"),  # fills the gap
        OrderEvent("order-1", 2, "PAID"),     # redelivered duplicate
        OrderEvent("order-1", 3, "SHIPPED"),
        OrderEvent("order-1", 3, "SHIPPED"),  # another duplicate
    ]
    for e in deliveries:
        store.receive(e)
        print(f"    state -> {store.describe('order-1')}")

    applied = " -> ".join(f"v{e.version} {e.status}"
                          for e in store.orders["order-1"].history)
    print(f"\n  Applied order: {applied}")
    assert [e.version for e in store.orders["order-1"].history] == [1, 2, 3]
    print("  RESULT: PASS (applied in version order, duplicates ignored)")


def scenario_2_missing_event():
    header("TEST 2: Missing event  (v1 delivered, v2 never arrives, v3 delivered)")
    store = OrderStore()
    store.receive(OrderEvent("order-2", 1, "CREATED"))
    store.receive(OrderEvent("order-2", 3, "SHIPPED"))

    print(f"\n  Final state: {store.describe('order-2')}")
    o = store.orders["order-2"]
    assert o.status == "CREATED" and o.version == 1
    assert 3 in o.pending
    assert store.missing_versions("order-2") == [2]
    print("  RESULT: PASS (order did NOT jump to SHIPPED; v2 recorded as "
          "missing, v3 held as pending)")


def scenario_3_stale_worker():
    header("TEST 3: Stale worker with fencing tokens")
    store = OrderStore()
    locks = LockService()

    store.receive(OrderEvent("order-3", 1, "CREATED"))

    token_a = locks.acquire("worker-A", "order-3")
    print("  ...        worker-A stalls (long GC pause / network partition); "
          "its lease expires")
    token_b = locks.acquire("worker-B", "order-3")

    store.write_with_token("worker-B", token_b,
                           OrderEvent("order-3", 2, "PAID"))
    print(f"    state -> {store.describe('order-3')}")

    print("  ...        worker-A wakes up, still thinks it holds the lease, "
          "and tries to write")
    rejected = False
    try:
        store.write_with_token("worker-A", token_a,
                               OrderEvent("order-3", 2, "CANCELLED"))
    except StaleTokenError:
        rejected = True
    print(f"    state -> {store.describe('order-3')}")

    assert rejected
    assert store.orders["order-3"].status == "PAID"
    print("  RESULT: PASS (newer token accepted, older token rejected)")


if __name__ == "__main__":
    scenario_1_ordering_and_duplicates()
    scenario_2_missing_event()
    scenario_3_stale_worker()
    print("\nAll scenarios passed.")
