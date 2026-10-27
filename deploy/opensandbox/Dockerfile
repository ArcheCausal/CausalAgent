ARG OPENSANDBOX_CODE_INTERPRETER_IMAGE=docker.io/opensandbox/code-interpreter@sha256:133a3c1720dd52291a019740c2987e7164ea6de79e23d8198798e58950ae2e6e
FROM ${OPENSANDBOX_CODE_INTERPRETER_IMAGE}

USER root
COPY requirements.lock /opt/causalagent/requirements.lock
RUN ["/bin/bash", "-euo", "pipefail", "-c", "source /opt/code-interpreter/code-interpreter-env.sh python 3.11 && python -m venv --system-site-packages /opt/causalagent/venv && /opt/causalagent/venv/bin/python -m pip install --no-cache-dir --require-hashes -r /opt/causalagent/requirements.lock && /opt/causalagent/venv/bin/python -c 'import sys, numpy, pandas, scipy, sklearn; assert sys.version_info[:2] == (3, 11)' && /opt/causalagent/venv/bin/python -m pip check && mv /opt/code-interpreter/code-interpreter-env.sh /opt/code-interpreter/code-interpreter-env.upstream.sh"]
COPY code-interpreter-env.sh /opt/code-interpreter/code-interpreter-env.sh

ENV PYTHON_VERSION=3.11
ENV PATH=/opt/causalagent/venv/bin:$PATH
