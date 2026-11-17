"""One mechanism for everything the project plugs into labkit: systems, judges, and measures.

The project registers a function under a name (a decorator); experiment configs refer to it by name
(`system: rag`, `judge: {name: ...}`); labkit.yaml lists the modules to import (`modules:`) so the
registrations run before labkit looks anything up. See labkit.plugins.
"""


class Registry(dict):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind

    def register(self, name: str):
        def decorator(obj):
            if name in self and self[name] is not obj:
                raise ValueError(f"{self.kind} {name!r} is already registered")
            self[name] = obj
            return obj
        return decorator

    def lookup(self, name):
        if name not in self:
            raise KeyError(f"unknown {self.kind} {name!r}; registered: {sorted(self) or 'none'}. "
                           "Is the module that registers it listed under `modules:` in labkit.yaml?")
        return self[name]
