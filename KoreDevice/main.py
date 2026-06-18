# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreDevice.
#
# Delegates to KoreDeviceGateway/main.py via runpy so the gateway (and its child sub-services)
# can be started from the KoreDevice directory root with:  python ./main.py
# ====================================================================================================
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "KoreDeviceGateway"))
runpy.run_path(os.path.join(os.path.dirname(__file__), "KoreDeviceGateway", "main.py"), run_name="__main__")

