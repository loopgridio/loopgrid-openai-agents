from __future__ import annotations

import importlib.util
import sys
import types

# Repository-only semantic tests can run before dependencies are installed. When the real
# dependencies are present, do NOT shadow them: release validation must exercise their public APIs.
if importlib.util.find_spec("agents") is None:
    agents = types.ModuleType("agents")
    class TracingProcessor:
        pass
    class RunHooks:
        pass
    agents.TracingProcessor = TracingProcessor
    agents.RunHooks = RunHooks
    agents.add_trace_processor = lambda processor: None
    sys.modules["agents"] = agents

if importlib.util.find_spec("loopgrid") is None:
    loopgrid = types.ModuleType("loopgrid")
    class LoopGrid:
        def __init__(self, *args, **kwargs):
            pass
    loopgrid.LoopGrid = LoopGrid
    sys.modules["loopgrid"] = loopgrid
