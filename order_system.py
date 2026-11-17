"""
Order-processing simulation with versioned events and fencing tokens.

An order moves through versioned states:

    version 1 -> CREATED
    version 2 -> PAID
    version 3 -> SHIPPED

Events may arrive late, out of order, or more than once. The OrderStore
applies each order's events strictly in version order, buffers events that
arrive early, and drops duplicates. Writes from workers are guarded by a
fencing token so that a stale worker cannot overwrite newer state.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderEvent:
    order_id: str
    version: int
    status: str

    def __str__(self):
        return f"{self.order_id} v{self.version} {self.status}"


@dataclass
class OrderState:
    order_id: str
    version: int = 0               # last version applied (0 = nothing yet)
    status: str = "NONE"
    pending: dict = field(default_factory=dict)   # version -> OrderEvent
    history: list = field(default_factory=list)   # applied events, in order
    highest_token: int = 0         # highest fencing token that has written


class StaleTokenError(Exception):
    pass


class OrderStore:
    def __init__(self, log=print):
        self.orders = {}
        self.log = log

    def _get(self, order_id):
        if order_id not in self.orders:
            self.orders[order_id] = OrderState(order_id)
        return self.orders[order_id]

    # ------------------------------------------------------------------
    # Versioned event ordering
    # ------------------------------------------------------------------
    def receive(self, event):
        """Handle one delivered event. Returns 'applied', 'buffered' or 'duplicate'."""
        order = self._get(event.order_id)
        self.log(f"  RECEIVED  {event}")

        # Already applied, or already waiting in the buffer -> duplicate.
        if event.version <= order.version or event.version in order.pending:
            self.log(f"  DUPLICATE {event} ignored "
                     f"(current version = {order.version})")
            return "duplicate"

        # Not the next expected version -> hold it until the gap is filled.
        if event.version != order.version + 1:
            order.pending[event.version] = event
            missing = self.missing_versions(event.order_id)
            self.log(f"  BUFFERED  {event} -- waiting for missing "
                     f"version(s) {missing}")
            return "buffered"

        # Next in sequence: apply it, then drain anything now unblocked.
        self._apply(order, event)
        while order.version + 1 in order.pending:
            self._apply(order, order.pending.pop(order.version + 1))
        return "applied"

    def _apply(self, order, event):
        old = order.status
        order.version = event.version
        order.status = event.status
        order.history.append(event)
        self.log(f"  APPLIED   {event}  ({old} -> {event.status})")

    def missing_versions(self, order_id):
        order = self._get(order_id)
        if not order.pending:
            return []
        top = max(order.pending)
        return [v for v in range(order.version + 1, top)
                if v not in order.pending]

    # ------------------------------------------------------------------
    # Fencing-token-guarded writes
    # ------------------------------------------------------------------
    def write_with_token(self, worker, token, event):
        """Apply an update only if `token` is not older than any token
        already seen for this order."""
        order = self._get(event.order_id)
        if token < order.highest_token:
            self.log(f"  REJECTED  {worker} token={token} {event} -- stale token "
                     f"(highest seen = {order.highest_token})")
            raise StaleTokenError(
                f"{worker} token {token} < {order.highest_token}")
        order.highest_token = token
        self.log(f"  ACCEPTED  {worker} token={token} {event}")
        return self.receive(event)

    def describe(self, order_id):
        o = self._get(order_id)
        pending = sorted(o.pending)
        return (f"{order_id}: status={o.status} version={o.version} "
                f"pending={pending} missing={self.missing_versions(order_id)}")


class LockService:
    """Hands out a lease on an order together with a monotonically
    increasing fencing token. A newer lease supersedes an older one."""

    def __init__(self, log=print):
        self.next_token = 0
        self.holder = {}
        self.log = log

    def acquire(self, worker, order_id):
        self.next_token += 1
        self.holder[order_id] = (worker, self.next_token)
        self.log(f"  LOCK      {worker} acquired lease on {order_id} "
                 f"with fencing token {self.next_token}")
        return self.next_token
