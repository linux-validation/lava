# SPDX-License-Identifier: GPL-2.0-or-later

# Join the branch's worker capacity and queue statistics migrations, which end
# in 0073_merge_queue_stats_and_tag_name, with upstream's 2026.10 migrations
# 0071_lava_log_entry .. 0074_testjob_add_missing_permissions. Both follow
# 0070_increase_tag_name_max_length. The branch migrations keep their names so
# that databases which already applied them do not run them again.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("lava_scheduler_app", "0073_merge_queue_stats_and_tag_name"),
        ("lava_scheduler_app", "0074_testjob_add_missing_permissions"),
    ]

    operations = []
