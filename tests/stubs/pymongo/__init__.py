ASCENDING = 1
DESCENDING = -1
class PyMongoError(Exception): pass
class _Errors:
    PyMongoError = PyMongoError
import sys as _s, types as _t
_e = _t.ModuleType("pymongo.errors"); _e.PyMongoError = PyMongoError
_s.modules["pymongo.errors"] = _e
errors = _e
