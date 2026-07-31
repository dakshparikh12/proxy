"""In-process LRU cache for user profiles."""

from collections import OrderedDict

CAPACITY = 128


class LRUCache:
    """Bounded LRU: evicts the least-recently-used entry past CAPACITY."""

    def __init__(self, capacity: int = CAPACITY) -> None:
        self._capacity = capacity
        self._entries: OrderedDict[str, object] = OrderedDict()

    def get(self, key: str) -> object | None:
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key]

    def put(self, key: str, value: object) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        """Drop one entry; True if it was present."""
        return self._entries.pop(key, None) is not None
