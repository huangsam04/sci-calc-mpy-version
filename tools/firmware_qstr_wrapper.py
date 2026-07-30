"""Run MicroPython's qstr generator from a response-style command file."""

import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys


def _split_command(command):
    if os.name != "nt":
        return shlex.split(command)

    import ctypes
    from ctypes import wintypes

    count = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    command_line_to_argv.restype = ctypes.POINTER(wintypes.LPWSTR)
    values = command_line_to_argv(command, ctypes.byref(count))
    if not values:
        raise ctypes.WinError()
    try:
        return [values[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(values)


def _map_qstr_lines(data, quote):
    output = bytearray()
    for line in data.splitlines(keepends=True):
        end = len(line)
        while end and line[end - 1] in (10, 13):
            end -= 1
        body = line[:end]
        ending = line[end:]
        if quote and body.startswith(b"Q("):
            body = b'"' + body + b'"'
        elif not quote and body.startswith(b'"Q(') and body.endswith(b')"'):
            body = body[1:-1]
        output.extend(body)
        output.extend(ending)
    return bytes(output)


def _run_pipeline(command_file):
    command = Path(command_file).read_text(encoding="utf-8")
    parts = command.split(" | ")
    if len(parts) != 4 or not parts[0].startswith("cat "):
        raise SystemExit("unsupported qstr preprocessor pipeline")
    tail, separator, output_text = parts[3].rpartition(" > ")
    if not separator or "sed " not in parts[1] or "sed " not in tail:
        raise SystemExit("incomplete qstr preprocessor pipeline")

    input_paths = _split_command(parts[0])[1:]
    compiler = _split_command(parts[2])
    outputs = _split_command(output_text)
    if not input_paths or not compiler or len(outputs) != 1:
        raise SystemExit("invalid qstr preprocessor pipeline arguments")

    source = b"".join(Path(path).read_bytes() for path in input_paths)
    result = subprocess.run(
        compiler,
        input=_map_qstr_lines(source, True),
        stdout=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        return result.returncode
    Path(outputs[0]).write_bytes(_map_qstr_lines(result.stdout, False))
    return 0


def main(arguments):
    if len(arguments) == 2 and arguments[0] == "--pipeline":
        return _run_pipeline(arguments[1])
    if len(arguments) != 1:
        raise SystemExit(
            "usage: firmware_qstr_wrapper.py [--pipeline] COMMAND_FILE")
    wrapped = _split_command(Path(arguments[0]).read_text(encoding="utf-8"))
    if len(wrapped) < 2:
        raise SystemExit("qstr command file contains no Python script")
    sys.argv = wrapped[1:]
    runpy.run_path(wrapped[1], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
