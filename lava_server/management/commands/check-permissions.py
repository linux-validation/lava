# Copyright (C) 2023 Collabora Limited
#
# Author: Igor Ponomarev <igor.ponomarev@collabora.com>
#
# This file is part of Lava Server.
#
# Lava Server is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation
#
# Lava Server is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Lava Server.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import BaseCommand, CommandError

from lava_scheduler_app.models import TestJob

if TYPE_CHECKING:
    from argparse import ArgumentParser


class Command(BaseCommand):
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("permissions_type", choices=("viewing_groups",))

    def handle(self, permissions_type: str, **options) -> None:
        if permissions_type == "viewing_groups":
            print_viewing_groups_statistics()
        else:
            raise CommandError


def print_viewing_groups_statistics() -> None:
    all_jobs = TestJob.objects.all()

    jobs_with_viewing_groups = all_jobs.filter(viewing_groups__isnull=False)
    number_of_jobs_with_viewing_groups = len(jobs_with_viewing_groups)
    number_of_finished_viewing_groups = len(
        jobs_with_viewing_groups.filter(state=TestJob.STATE_FINISHED)
    )
    number_of_unfinished_jobs = (
        number_of_jobs_with_viewing_groups - number_of_finished_viewing_groups
    )

    print(
        f"Found {number_of_jobs_with_viewing_groups} jobs affected by viewing groups."
    )
    print(f"Finished jobs: {number_of_finished_viewing_groups}")
    print(f"Unfinished jobs: {number_of_unfinished_jobs}")
