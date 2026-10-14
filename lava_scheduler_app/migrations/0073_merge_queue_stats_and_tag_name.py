# SPDX-License-Identifier: GPL-2.0-or-later

# Join the branch's worker capacity and queue statistics migrations
# (0070_worker_capacity_stats .. 0072_device_type_snapshot_duration) with
# upstream's 0070_increase_tag_name_max_length, which both follow
# 0069_testjob_metadata_gin_index. The branch migrations keep their names so
# that databases which already applied them do not run them again.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("lava_scheduler_app", "0070_increase_tag_name_max_length"),
        ("lava_scheduler_app", "0072_device_type_snapshot_duration"),
    ]

    operations = []
