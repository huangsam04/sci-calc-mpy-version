import os
import sys


def test_reports_the_session_temp_root(tmp_path_factory):
    print("SCI_CALC_PYTEST_BASE=" + str(tmp_path_factory.getbasetemp()))
    print("SCI_CALC_PROCESS_TEMP=" + os.environ["TEMP"])
    print("SCI_CALC_PROCESS_TMP=" + os.environ["TMP"])
    print("SCI_CALC_PROCESS_TMPDIR=" + os.environ["TMPDIR"])
    print("SCI_CALC_PYTHON_CACHE=" + str(sys.pycache_prefix))
    if os.environ.get("SCI_CALC_PYTEST_PROBE_FAIL") == "1":
        raise RuntimeError("intentional temp cleanup probe failure")
