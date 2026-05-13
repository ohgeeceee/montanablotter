import sys

from services.agents import mission_control as _mission_control

sys.modules[__name__] = _mission_control
