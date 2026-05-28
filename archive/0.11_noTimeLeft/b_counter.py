import marimo

__generated_with = "0.23.6"
app = marimo.App()

with app.setup:
    """Per-resource viewer counting with two thresholds.

    Three tiers of service based on per-resource viewer count:

      0 to soft_cap         → live SSE
      soft_cap to hard_cap  → polling (interval ramps with overage)
      above hard_cap        → static (no automatic updates)
    """

    import threading
    from contextlib import contextmanager





@app.cell
def _():
    import marimo as mo

    return


@app.class_definition
class LiveCounter:
    """Tracks how many viewers are watching each resource."""

    def __init__(self, soft_cap=200, hard_cap=None,
                 min_poll_ms=1_000, max_poll_ms=8_000, ramp_users=50):
        self.soft_cap = soft_cap
        self.hard_cap = hard_cap
        self.min_poll_ms = min_poll_ms
        self.max_poll_ms = max_poll_ms
        self.ramp_users = ramp_users
        self._counts = {}
        self._lock = threading.Lock()

    def count(self, resource_id):
        with self._lock:
            return self._counts.get(resource_id, 0)

    def mode(self, resource_id):
        """Return current service mode: 'live', 'poll', or 'static'."""
        count = self.count(resource_id)
        if count < self.soft_cap:
            return "live"
        if self.hard_cap is not None and count >= self.hard_cap:
            return "static"
        return "poll"

    def should_be_live(self, resource_id):
        return self.mode(resource_id) == "live"

    def poll_interval_ms(self, resource_id):
        count = self.count(resource_id)
        if count <= self.soft_cap:
            return self.min_poll_ms
        overage = count - self.soft_cap
        frac = min(1.0, overage / self.ramp_users)
        return int(self.min_poll_ms + frac * (self.max_poll_ms - self.min_poll_ms))

    @contextmanager
    def join(self, resource_id):
        with self._lock:
            self._counts[resource_id] = self._counts.get(resource_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                new = self._counts.get(resource_id, 0) - 1
                if new <= 0:
                    self._counts.pop(resource_id, None)
                else:
                    self._counts[resource_id] = new

    def snapshot(self):
        with self._lock:
            return dict(self._counts)


if __name__ == "__main__":
    app.run()
