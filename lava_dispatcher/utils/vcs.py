# Copyright (C) 2014 Linaro Limited
#
# Author: Remi Duraffort <remi.duraffort@linaro.org>
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import logging
import os
import shutil
import subprocess  # nosec - internal use.
from pathlib import Path

from lava_common.exceptions import InfrastructureError
from lava_dispatcher.utils.decorator import retry


class VCSHelper:
    def __init__(self, url: str):
        self.url = url

    def clone(
        self,
        dest_path: str,
        shallow: bool = False,
        revision: str | None = None,
        branch: str | None = None,
        history: bool = True,
        recursive: bool = False,
    ) -> str:
        raise NotImplementedError


class GitHelper(VCSHelper):
    """
    Helper to clone a git repository.

    Usage:
      git = GitHelper('url_to.git')
      commit_id = git.clone('destination')
      commit_id = git.clone('destination2, 'hash')

    This helper will raise a InfrastructureError for any error encountered.
    """

    def __init__(self, url: str):
        super().__init__(url)
        self.binary = "/usr/bin/git"

    @retry(exception=InfrastructureError, retries=6, delay=5)
    def clone(
        self,
        dest_path: str,
        shallow: bool = False,
        revision: str | None = None,
        branch: str | None = None,
        history: bool = True,
        recursive: bool = False,
    ) -> str:
        logger = logging.getLogger("dispatcher")

        # Clear the data
        if os.path.exists(dest_path):
            shutil.rmtree(dest_path)

        try:
            cmd_args: list[str] = [self.binary, "clone"]
            if recursive:
                cmd_args.append("--recurse-submodules")
            if branch is not None:
                cmd_args.extend(["-b", branch])
            if shallow:
                cmd_args.append("--depth=1")
            cmd_args.extend([self.url, dest_path])

            # use SSH key config of lavaserver user, thus prepare directory and config for this user to be writable
            Path(dest_path).mkdir(parents=True)
            shutil.chown(dest_path, user="lavaserver")
            subprocess.run([self.binary, "config", "--global", "--add", "safe.directory", dest_path], user="lavaserver", env={"HOME": "/var/lib/lava-server/home"})

            logger.debug("Running '%s'", " ".join(cmd_args))
            # Replace shell variables by the corresponding environment variable
            cmd_args[-2] = os.path.expandvars(cmd_args[-2])

            try:
                subprocess.run(  # nosec - internal use.
                    cmd_args, check=True, stderr=subprocess.STDOUT, user="lavaserver", env={"HOME": "/var/lib/lava-server/home"}
                )
            except subprocess.CalledProcessError as exc:
                if (
                    exc.stdout
                    and "does not support shallow capabilities"
                    in exc.stdout.decode("utf-8", errors="replace")
                ):
                    logger.warning(
                        "Tried shallow clone, but server doesn't support it. Retrying without..."
                    )
                    cmd_args.remove("--depth=1")
                    subprocess.run(  # nosec - internal use.
                        cmd_args, check=True, stderr=subprocess.STDOUT, user="lavaserver", env={"HOME": "/var/lib/lava-server/home"}
                    )
                else:
                    raise

            if revision is not None:
                logger.debug("Running '%s checkout %s", self.binary, str(revision))
                subprocess.run(  # nosec - internal use.
                    [self.binary, "-C", dest_path, "checkout", str(revision)],
                    check=True, stderr=subprocess.STDOUT, user="lavaserver", env={"HOME": "/var/lib/lava-server/home"}
                )

            commit_id = subprocess.run(  # nosec - internal use.
                [self.binary, "-C", dest_path, "log", "-1", "--pretty=%H"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, user="lavaserver", env={"HOME": "/var/lib/lava-server/home"}
            ).stdout.strip()

            if not history:
                logger.debug("Removing '.git' directory in %s", dest_path)
                shutil.rmtree(os.path.join(dest_path, ".git"))

        except subprocess.CalledProcessError as exc:
            if exc.stdout:
                logger.warning(exc.stdout.decode("utf-8", errors="replace"))
            if exc.stderr:
                logger.error(exc.stderr.decode("utf-8", errors="replace"))
            raise InfrastructureError(
                "Unable to fetch git repository '%s'" % (self.url)
            )
        finally:
            # cleanup config to avoid clogging it
            subprocess.run([self.binary, "config", "--global", "--unset", "safe.directory", dest_path], user="lavaserver", env={"HOME": "/var/lib/lava-server/home"})

        return commit_id.decode("utf-8", errors="replace")


class TarHelper(VCSHelper):
    # TODO: implement TarHelper

    def __init__(self, url: str):
        super().__init__(url)
        self.binary: str | None = None


class URLHelper(VCSHelper):
    # TODO: implement URLHelper

    def __init__(self, url: str):
        super().__init__(url)
        self.binary: str | None = None

    def clone(
        self,
        dest_path: str,
        shallow: bool = False,
        revision: str | None = None,
        branch: str | None = None,
        history: bool = True,
        recursive: bool = False,
    ) -> str:
        raise NotImplementedError
