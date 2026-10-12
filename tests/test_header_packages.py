from doppel.sandbox import header_packages

# error text from real runs
COMPILER = "src/ldap.c:3:10: fatal error: lber.h: No such file or directory"           # mealie
PKG_CONFIG = "Package xmlsec1 was not found in the pkg-config search path."            # python-xmlsec
WEBLATE_BORG = ("Exception: Could not find lz4 lib/headers, please set BORG_LIBLZ4_PREFIX "
                "or ensure liblz4.pc is in PKG_CONFIG_PATH.")                           # weblate round2


def test_compiler_header_miss_still_works():
    assert "libldap2-dev" in header_packages(COMPILER)


def test_pkg_config_miss():
    assert "libxmlsec1-dev" in header_packages(PKG_CONFIG)


def test_borgbackup_gets_its_libraries():
    pkgs = header_packages(WEBLATE_BORG)
    for pkg in ("liblz4-dev", "libzstd-dev", "libxxhash-dev"):
        assert pkg in pkgs, pkg


def test_unrelated_output_installs_nothing():
    assert header_packages("ERROR: The --python option must be placed before the pip subcommand name") == []


ICU = "subprocess.CalledProcessError: Command '('pkg-config', '--modversion', 'icu-i18n')' returned non-zero exit status 1."


def test_pyicu_gets_libicu():
    assert "libicu-dev" in header_packages(ICU)
