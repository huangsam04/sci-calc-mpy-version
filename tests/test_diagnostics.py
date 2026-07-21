from diagnostics import DiagnosticSession
from utils import storage


def test_serial_diagnostic_session_traces_input_navigation_and_results(tmp_path):
    storage.configure_storage(str(tmp_path))
    output = []
    session = DiagnosticSession(output.append)

    session.execute("KEY 3 1 0")
    session.execute("KEY 3 3 0")
    session.execute("BACK")
    session.execute("EVAL 2+3*4")
    session.execute("PANEL")

    assert any("key=2" in line and "selection=1" in line for line in output)
    assert any("key=ENT" in line and "page=Plot" in line for line in output)
    assert any("page=MainMenu" in line for line in output)
    assert any("expr=2+3*4 result=14" in line for line in output)
    assert any("id=basic" in line and "Arithmetic" in line for line in output)
