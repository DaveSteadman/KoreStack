
# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests owned by KoreTest that exercise KoreAgent as the system under test.
# ====================================================================================================

import sys
from pathlib import Path


AGENT_APP_ROOT = Path(__file__).resolve().parents[3] / "KoreAgent" / "app"
if str(AGENT_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_APP_ROOT))
