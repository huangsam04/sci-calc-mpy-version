import os


def test_reports_the_session_temp_root(tmp_path_factory):
    print("SCI_CALC_PYTEST_BASE=" + str(tmp_path_factory.getbasetemp()))
    if os.environ.get("SCI_CALC_PYTEST_PROBE_FAIL") == "1":
        raise RuntimeError("intentional temp cleanup probe failure")
