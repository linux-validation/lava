# Copyright (C) 2019 Matthew Hart
#
# Author: Matthew Hart <matt@mattface.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import requests

from lava_common.exceptions import InfrastructureError, JobError


def pdudaemon_reboot(daemon, alias, port=16421, delay=5):
    return pdudaemon_request(daemon, alias, "reboot", port, delay)


def pdudaemon_off(daemon, alias, port=16421, delay=5):
    return pdudaemon_request(daemon, alias, "off", port, delay)


def pdudaemon_on(daemon, alias, port=16421, delay=5):
    return pdudaemon_request(daemon, alias, "on", port, delay)


def pdudaemon_request(daemon, alias, request, port, delay):
    url = "http://{}:{}/power/control/{}?alias={}&delay={}".format(
        daemon, port, request, alias, delay
    )
    r = requests.get(url)
    return r
