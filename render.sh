#!/bin/sh
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
task_python="${SCOPE_PYTHON:-$task_root/../scopeData_FFT/.venv/bin/python}"
exec "$task_python" "$task_root/render_tss.py" "$@"
