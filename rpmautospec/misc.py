from functools import cmp_to_key
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional
from typing import Tuple
from typing import Union
import typing

import koji
import rpm


# The %autorelease macro including parameters. This is imported into the main package to be used
# from 3rd party code like fedpkg etc.
AUTORELEASE_MACRO = "autorelease(e:s:hp)"
AUTORELEASE_SENTINEL = "__AUTORELEASE_SENTINEL__"

evr_re = re.compile(r"^(?:(?P<epoch>\d+):)?(?P<version>[^-:]+)(?:-(?P<release>[^-:]+))?$")
autochangelog_re = re.compile(r"\s*%(?:autochangelog|\{\??autochangelog\})\s*")

rpmvercmp_key = cmp_to_key(
    lambda b1, b2: rpm.labelCompare(
        (str(b1["epoch"]), b1["version"], b1["release"]),
        (str(b2["epoch"]), b2["version"], b2["release"]),
    ),
)

_kojiclient = None

_log = logging.getLogger(__name__)


def parse_evr(evr_str: str) -> Tuple[int, str, Optional[str]]:
    match = evr_re.match(evr_str)

    if not match:
        raise ValueError(evr_str)

    epoch = match.group("epoch") or 0
    epoch = int(epoch)

    return epoch, match.group("version"), match.group("release")


def get_rpm_current_version(path: str, name: Optional[str] = None, with_epoch: bool = False) -> str:
    """Retrieve the current version set in the spec file named ``name``.spec
    at the given path.
    """
    path = Path(path)

    if not name:
        name = path.name

    specfile = path / f"{name}.spec"

    if not specfile.exists():
        return None

    query = "%{version}"
    if with_epoch:
        query = "%|epoch?{%{epoch}:}:{}|" + query
    query += r"\n"

    rpm_cmd = [
        "rpm",
        "--define",
        "_invalid_encoding_terminates_build 0",
        "--define",
        f"{AUTORELEASE_MACRO} 1%{{?dist}}",
        "--define",
        "autochangelog %nil",
        "--qf",
        query,
        "--specfile",
        f"{name}.spec",
    ]

    output = None
    try:
        output = run_command(rpm_cmd, cwd=path).decode("UTF-8").split("\n")[0].strip()
    except Exception:
        pass
    return output


def koji_init(koji_url_or_session: Union[str, koji.ClientSession]) -> koji.ClientSession:
    global _kojiclient
    if isinstance(koji_url_or_session, str):
        _kojiclient = koji.ClientSession(koji_url_or_session)
    else:
        _kojiclient = koji_url_or_session
    return _kojiclient


def query_current_git_commit_hash(
    path: str,
    log_options: typing.Optional[typing.List[str]] = None,
):
    """Retrieves the git commit hash in ``path`` .

    This method runs `git log -1 --format="%H"` at ``path``

    This command returns a commit hash number like the following:
    1e86efac2723289c896165bae2e863cb66466376
    ...
    """
    _log.debug("query_current_git_commit_hash(): %s", path)

    cmd = ["git", "log", "-1", "--format=%H"]
    if log_options:
        cmd.extend(log_options)

    _log.debug("query_current_git_commit_hash(): %s", cmd)
    return run_command(cmd, cwd=path).decode("UTF-8").strip()


def checkout_git_commit(
    path: str,
    commit: str,
    log_options: typing.Optional[typing.List[str]] = None,
) -> typing.List[str]:
    """Checks out the git commit in ``path`` specified in ``commit``.

    This method runs the system's `xxxx` command.
    ...
    """
    _log.debug("checkout_git_commit(): %s", path)
    _log.debug("checkout_git_commit(): %s", commit)

    cmd = ["git", "checkout", commit]
    if log_options:
        cmd.extend(log_options)

    _log.debug("checkout_git_commit(): %s", cmd)
    subprocess.check_output(cmd, cwd=path, stderr=subprocess.PIPE)
    return query_current_git_commit_hash(path)


def run_command(command: list, cwd: Optional[str] = None) -> bytes:
    """Run the specified command in a specific working directory if one
    is specified.
    """
    output = None
    try:
        output = subprocess.check_output(command, cwd=cwd, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        _log.error("Command `%s` return code: `%s`", " ".join(command), e.returncode)
        _log.error("stdout:\n-------\n%s", e.stdout)
        _log.error("stderr:\n-------\n%s", e.stderr)
        raise

    return output


def specfile_uses_rpmautospec(
    specfile: str, check_autorelease: bool = True, check_autochangelog: bool = True
) -> bool:
    """Check whether or not an RPM spec file uses rpmautospec features."""

    autorelease = check_autorelease_presence(specfile)
    autochangelog = check_autochangelog_presence(specfile)

    if check_autorelease and check_autochangelog:
        return autorelease or autochangelog
    elif check_autorelease:
        return autorelease
    elif check_autochangelog:
        return autochangelog
    else:
        raise ValueError("One of check_autorelease and check_autochangelog must be set")


def check_autorelease_presence(filename: str) -> bool:
    """
    Use the rpm package to detect the presence of an
    autorelease macro and return true if found.
    """
    cmd = (
        "rpm",
        "--define",
        "{} {}".format(AUTORELEASE_MACRO, AUTORELEASE_SENTINEL),
        "-q",
        "--queryformat",
        "%{release}\n",
        "--specfile",
        filename,
    )
    popen = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    release = popen.communicate()[0].decode(errors="replace").split("\n")[0]
    return release == AUTORELEASE_SENTINEL


def check_autochangelog_presence(filename: str) -> bool:
    """
    Search for the autochangelog macro and return true if found.
    """
    with open(filename, "r") as specfile:
        for _, line in enumerate(iter(specfile), start=1):
            line = line.rstrip("\n")
            if autochangelog_re.match(line):
                return True
        return False
