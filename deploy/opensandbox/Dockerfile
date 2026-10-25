ARG OPENSANDBOX_CODE_INTERPRETER_IMAGE
FROM ${OPENSANDBOX_CODE_INTERPRETER_IMAGE}

USER root
COPY requirements.lock /opt/causalagent/requirements.lock
RUN ["/bin/bash", "-euo", "pipefail", "-c", "source /opt/code-interpreter/code-interpreter-env.sh python 3.11 && python -m pip install --no-cache-dir --require-hashes -r /opt/causalagent/requirements.lock && python -c 'import sys, numpy, pandas, scipy, sklearn; assert sys.version_info[:2] == (3, 11)' && python -m pip check"]

ENV PYTHON_VERSION=3.11
