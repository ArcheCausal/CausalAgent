"""Read a mounted token without putting its value in Compose or command arguments."""

import os
from pathlib import Path
import stat


def main() -> None:
    path = Path(os.environ["CAUSALAGENT_SANDBOX_API_KEY_FILE"])
    if stat.S_IMODE(path.stat().st_mode) not in (0o400, 0o600, 0o640):
        raise ValueError("OpenSandbox token file must have mode 0400, 0600 or 0640")
    token = path.read_text(encoding="utf-8").strip()
    if not 32 <= len(token) <= 4096 or any(c.isspace() for c in token):
        raise ValueError("OpenSandbox token file must contain one bare token of 32-4096 characters")
    os.environ["OPENSANDBOX_SERVER_API_KEY"] = token
    os.execvp("opensandbox-server", ["opensandbox-server", "--config", os.environ["SANDBOX_CONFIG_PATH"]])


if __name__ == "__main__":
    main()
