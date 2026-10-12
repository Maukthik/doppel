from doppel.sandbox import needs_build_tools

# error text copied from the round2 run
NETBOX = ("couldn't run 'pg_config' --includedir: [Errno 2] No such file or directory: 'pg_config'\n"
          "error: [Errno 2] No such file or directory: 'pg_config'")
WEBLATE = "OSError: pkg-config probably not installed: FileNotFoundError(2, 'No such file or directory')"
LITELLM = "ERROR: The --python option must be placed before the pip subcommand name"


def test_missing_pg_config_triggers_build_tools():
    assert needs_build_tools(NETBOX)


def test_missing_pkg_config_triggers_build_tools():
    assert needs_build_tools(WEBLATE)


def test_command_mistake_does_not_trigger_build_tools():
    assert not needs_build_tools(LITELLM)
