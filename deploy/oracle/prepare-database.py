"""Create private database settings on the target VM; never prints credentials."""

import argparse
import os
from pathlib import Path
import secrets


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    targets = [directory / "postgres.env", directory / "database-url"]
    if any(path.exists() for path in targets):
        if all(path.is_file() for path in targets):
            return False
        raise RuntimeError("Incomplete database settings exist; inspect them before retrying.")
    admin_password, app_password = secrets.token_hex(32), secrets.token_hex(32)
    values = [
        f"POSTGRES_PASSWORD={admin_password}\nBASKET_APP_PASSWORD={app_password}\n",
        f"postgresql://basket_owner:{app_password}@database:5432/basket\n",
    ]
    for path, value in zip(targets, values):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(value)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default="/opt/basket/config")
    args = parser.parse_args()
    created = prepare(args.config_dir)
    print("Private database settings created." if created else "Existing database settings preserved.")


if __name__ == "__main__":
    main()
