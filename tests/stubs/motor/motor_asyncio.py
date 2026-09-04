"""Minimal motor stub. MODE controls whether Mongo is 'up' or 'down'."""
MODE = {"up": False, "docs": []}

class _Cursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None):
        if not MODE["up"]: raise ConnectionError("simulated: mongo unreachable")
        return list(MODE["docs"])
    def limit(self, n): return self
    def sort(self, *a, **k): return self

class _Collection:
    def __init__(self, name): self.name = name
    async def find_one(self, *a, **k):
        if not MODE["up"]: raise ConnectionError("simulated: mongo unreachable")
        flt = a[0] if a else {}
        for d in MODE["docs"]:
            if all(d.get(kk) == vv for kk, vv in flt.items() if not isinstance(vv, dict)):
                return dict(d)
        return None
    def find(self, *a, **k): return _Cursor(MODE["docs"])
    async def update_one(self, *a, **k):
        if not MODE["up"]: raise ConnectionError("simulated: mongo unreachable")
        return type("R", (), {"matched_count": 1})()
    async def create_index(self, *a, **k): return "idx"
    async def count_documents(self, *a, **k): return len(MODE["docs"])

class _DB:
    def get_collection(self, name): return _Collection(name)

class _Admin:
    async def command(self, *a, **k):
        if not MODE["up"]: raise ConnectionError("simulated: mongo unreachable")
        return {"ok": 1}

class AsyncIOMotorClient:
    def __init__(self, *a, **k): self.nodes = {("stub-host", 27017)}
    def get_database(self, name): return _DB()
    @property
    def admin(self): return _Admin()
