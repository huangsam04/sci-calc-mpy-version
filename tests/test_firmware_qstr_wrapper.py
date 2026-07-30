import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
WRAPPER = ROOT / "tools" / "firmware_qstr_wrapper.py"


def test_qstr_wrapper_runs_a_long_command_file_without_a_shell(tmp_path):
    output = tmp_path / "result.json"
    probe = tmp_path / "argument probe.py"
    probe.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    command_file = tmp_path / "qstr-command.txt"
    command_file.write_text(
        subprocess.list2cmdline(
            [sys.executable, str(probe), str(output), "two words", 'a"b']
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(WRAPPER), str(command_file)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == ["two words", 'a"b']


def test_qstr_wrapper_propagates_the_wrapped_script_exit_code(tmp_path):
    probe = tmp_path / "fail.py"
    probe.write_text("raise SystemExit(7)\n", encoding="utf-8")
    command_file = tmp_path / "qstr-command.txt"
    command_file.write_text(
        subprocess.list2cmdline([sys.executable, str(probe)]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(WRAPPER), str(command_file)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7


def test_qstr_wrapper_runs_the_preprocessor_pipeline_without_a_shell(tmp_path):
    first = tmp_path / "first qstr.h"
    second = tmp_path / "second qstr.h"
    output = tmp_path / "qstrdefs.preprocessed.h"
    first.write_bytes(b"Q(alpha)\nplain\n")
    second.write_bytes(b"Q(beta)\n")
    compiler = tmp_path / "compiler probe.py"
    compiler.write_text(
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        "assert b'\\\"Q(alpha)\\\"' in data\n"
        "sys.stdout.buffer.write(data)\n",
        encoding="utf-8",
    )
    command_file = tmp_path / "qstr-pipeline.txt"
    command_file.write_text(
        "cat "
        + subprocess.list2cmdline([str(first), str(second)])
        + ' | sed "s/^Q(.*)/\\\"&\\\"/" | '
        + subprocess.list2cmdline([sys.executable, str(compiler), "-"])
        + ' | sed "s/^\\\\\\\"\\(Q(.*)\\)\\\\\\\"/\\1/" > '
        + subprocess.list2cmdline([str(output)]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--pipeline", str(command_file)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_bytes() == first.read_bytes() + second.read_bytes()
