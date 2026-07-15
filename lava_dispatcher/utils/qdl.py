# Copyright 2026 Qualcomm Inc.
#
# Author: Milosz Wasilewski <milosz.wasilewski@oss.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later


import shlex

from lava_dispatcher.utils.containers import (
    DockerDriver,
    NullDriver,
    OptionalContainerAction,
)
from lava_dispatcher.utils.network import dispatcher_ip
from lava_dispatcher.utils.shell import which


class OptionalContainerQdlAction(OptionalContainerAction):
    @property
    def driver(self):
        __driver__ = getattr(self, "__driver__", None)
        if not __driver__:
            # The container is defined solely in the test job. When no `docker`
            # block is present, qdl runs natively on the worker.
            if "docker" in self.parameters:
                params = self.parameters["docker"]
                remote_options = params.get("remote_options", "")
                self.__driver__ = DockerDriver(self, params)
                self.__driver__.docker_options = shlex.split(remote_options)
                self.__driver__.docker_run_options = [
                    "--privileged",
                    "--volume=/dev:/dev",
                    "--net=host",
                ]
            else:
                self.__driver__ = NullDriver(self)
        return self.__driver__

    def which(self, path):
        if self.driver.is_container:
            return path
        return which(path)

    def parsed_qdl_command(self, cmd, **kwargs):
        # Run a one-shot qdl command (e.g. `qdl --version`) and return its
        # output. In container mode this uses a plain `docker run` without any
        # device mapping, so it works even before the board is enumerated.
        return self.parsed_command(self.driver.get_command_prefix() + cmd, **kwargs)

    def run_qdl(self, cmd, flash_dir, allow_fail=False, error_msg=None):
        return self.run_cmd(
            self.get_qdl_cmd(cmd, flash_dir),
            allow_fail,
            error_msg,
            cwd=flash_dir,
        )

    def get_qdl_cmd(self, cmd, flash_dir):
        if not self.driver.is_container:
            return cmd

        flash_dir = str(flash_dir)
        if self.driver.docker_options:
            # Remote docker daemon: the flashing files live on the dispatcher
            # and are not reachable via bind mount. Mount them over NFS inside
            # the container and run qdl from within that directory.
            ip_addr = dispatcher_ip(self.job.parameters["dispatcher"])
            wrapped = [
                "mkdir",
                "-p",
                flash_dir,
                "&&",
                "mount",
                "-t",
                "nfs",
                "-o",
                "nolock",
                f"{ip_addr}:{flash_dir}",
                flash_dir,
                "&&",
                "cd",
                flash_dir,
                "&&",
            ] + [str(c) for c in cmd]
            cmd = ["bash", "-c", " ".join(wrapped)]
        else:
            # Local docker daemon: bind mount the flashing directory and use
            # it as the working directory so the relative paths in the qdl
            # command resolve inside the container.
            self.driver.docker_run_options = [
                opt
                for opt in self.driver.docker_run_options
                if not opt.startswith(f"--volume={flash_dir}:")
                and not opt.startswith("--workdir=")
            ] + [
                f"--volume={flash_dir}:{flash_dir}",
                f"--workdir={flash_dir}",
            ]
        return self.driver.get_command_prefix() + cmd
