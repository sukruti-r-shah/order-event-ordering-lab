import unittest

from order_system import LockService, OrderEvent, OrderStore, StaleTokenError


def quiet(*_):
    pass


class OrderingTests(unittest.TestCase):
    def setUp(self):
        self.store = OrderStore(log=quiet)

    def test_out_of_order_is_reordered(self):
        self.assertEqual(self.store.receive(OrderEvent("o", 2, "PAID")), "buffered")
        self.assertEqual(self.store.orders["o"].status, "NONE")
        self.assertEqual(self.store.receive(OrderEvent("o", 1, "CREATED")), "applied")
        o = self.store.orders["o"]
        self.assertEqual([e.version for e in o.history], [1, 2])
        self.assertEqual(o.status, "PAID")

    def test_duplicate_after_apply_is_ignored(self):
        self.store.receive(OrderEvent("o", 1, "CREATED"))
        self.store.receive(OrderEvent("o", 2, "PAID"))
        self.assertEqual(self.store.receive(OrderEvent("o", 2, "PAID")), "duplicate")
        self.assertEqual(len(self.store.orders["o"].history), 2)

    def test_duplicate_while_pending_is_ignored(self):
        self.store.receive(OrderEvent("o", 2, "PAID"))
        self.assertEqual(self.store.receive(OrderEvent("o", 2, "PAID")), "duplicate")

    def test_missing_event_does_not_jump_ahead(self):
        self.store.receive(OrderEvent("o", 1, "CREATED"))
        self.store.receive(OrderEvent("o", 3, "SHIPPED"))
        o = self.store.orders["o"]
        self.assertEqual((o.version, o.status), (1, "CREATED"))
        self.assertEqual(self.store.missing_versions("o"), [2])

    def test_late_missing_event_unblocks_pending(self):
        self.store.receive(OrderEvent("o", 1, "CREATED"))
        self.store.receive(OrderEvent("o", 3, "SHIPPED"))
        self.store.receive(OrderEvent("o", 2, "PAID"))
        o = self.store.orders["o"]
        self.assertEqual((o.version, o.status), (3, "SHIPPED"))
        self.assertEqual(o.pending, {})


class FencingTests(unittest.TestCase):
    def test_stale_token_rejected(self):
        store = OrderStore(log=quiet)
        locks = LockService(log=quiet)
        store.receive(OrderEvent("o", 1, "CREATED"))
        a = locks.acquire("A", "o")
        b = locks.acquire("B", "o")
        self.assertGreater(b, a)
        store.write_with_token("B", b, OrderEvent("o", 2, "PAID"))
        with self.assertRaises(StaleTokenError):
            store.write_with_token("A", a, OrderEvent("o", 2, "CANCELLED"))
        self.assertEqual(store.orders["o"].status, "PAID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
