"""Read the shared secret file and export only the official server API key."""

import os
from config.secret_file import load_secrets


def main() -> None:
    secrets = load_secrets(required_keys=("CAUSALAGENT_SANDBOX_API_KEY",))
    os.environ["OPENSANDBOX_SERVER_API_KEY"] = secrets["CAUSALAGENT_SANDBOX_API_KEY"]
    os.execvp("opensandbox-server", ["opensandbox-server", "--config", os.environ["SANDBOX_CONFIG_PATH"]])


if __name__ == "__main__":
    main()
