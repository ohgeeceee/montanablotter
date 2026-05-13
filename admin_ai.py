import sys

from services.admin import ai as _admin_ai

sys.modules[__name__] = _admin_ai
