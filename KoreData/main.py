# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreData.
#
# Delegates to KoreDataGateway/main.py via runpy so the gateway (and its child sub-services)
# can be started from the KoreData directory root with:  python ./main.py
#
# Related modules:
#   - KoreDataGateway/main.py  -- actual startup logic, banner, and uvicorn launch
# ====================================================================================================
import sys, os, runpy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "KoreDataGateway"))
runpy.run_path(os.path.join(os.path.dirname(__file__), "KoreDataGateway", "main.py"), run_name="__main__")
