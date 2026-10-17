"""Start isso for Doppel's twin: the config next to this file, the port from $PORT (isso's own `run` takes it
from the config file, which can't read the environment)."""
import os

from isso import config, make_app
from werkzeug.serving import run_simple

here = os.path.dirname(os.path.abspath(__file__))
conf = config.load(config.default_file(), os.path.join(here, "isso-doppel.cfg"))
run_simple("0.0.0.0", int(os.environ["PORT"]), make_app(conf), threaded=True)
