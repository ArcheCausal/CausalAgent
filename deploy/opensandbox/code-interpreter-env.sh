#!/bin/bash
# Sourced by the official interpreter entrypoint, including its EXECD_ENVS setup.
source /opt/code-interpreter/code-interpreter-env.upstream.sh "$@"
if [ "${1:-}" = python ]; then
    if [ "${2:-3.11}" != 3.11 ]; then
        echo 'CausalAgent data preparation requires Python 3.11' >&2
        return 1
    fi
    export VIRTUAL_ENV=/opt/causalagent/venv
    export PATH="$VIRTUAL_ENV/bin:$PATH"
    if [ -n "${EXECD_ENVS:-}" ]; then
        printf 'PATH=%s\nVIRTUAL_ENV=%s\n' "$PATH" "$VIRTUAL_ENV" >>"$EXECD_ENVS"
    fi
fi
