import argparse
import os

parser = argparse.ArgumentParser(prog="dotd", description="Dot device daemon")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=7777)
parser.add_argument("--config", default=os.environ.get("DOT_CONFIG", "/etc/dot/config.json"))
parser.add_argument("--ui", default=os.environ.get("DOT_UI", "/usr/lib/dot/ui"))
parser.add_argument("--dev", action="store_true", help="fake hardware, for working on the UI on a laptop")
args = parser.parse_args()

if args.dev:
    os.environ["DOT_DEV"] = "1"
    # keep everything a real Pi writes under /run and /var/lib inside .dev/
    for key, path in (("DOT_INBOX", ".dev/inbox"), ("DOT_STANDBY_FILE", ".dev/standby.json"),
                      ("DOT_SWAY_OUTPUTS", ".dev/sway-outputs.conf"), ("DOT_INSTALL_DIR", ".dev/install")):
        os.environ.setdefault(key, path)

from .server import serve  # noqa: E402  (system reads DOT_DEV at import)

serve(args.host, args.port, args.config, args.ui)
