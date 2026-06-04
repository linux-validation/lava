# Copyright (C) 2024 Collabora Limited
#
# Author: Igor Ponomarev <igor.ponomarev@collabora.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from rest_framework_extensions.routers import ExtendedDefaultRouter

from . import views

router = ExtendedDefaultRouter()

# Re-export every v0.2 resource so v0.3 is a strict superset.
router.register(r"aliases", views.AliasViewSet)
router.register(r"devices", views.DeviceViewSet)
router.register(r"devicetypes", views.DeviceTypeViewSet)
jobs_router = router.register(r"jobs", views.TestJobViewSet)
jobs_router.register(
    r"tests",
    views.TestCaseViewSet,
    parents_query_lookups=["suite__job_id"],
    basename="jobs-tests",
)
jobs_router.register(
    r"suites",
    views.TestSuiteViewSet,
    parents_query_lookups=["job_id"],
    basename="jobs-suite",
).register(
    r"tests",
    views.TestCaseViewSet,
    parents_query_lookups=["suite__job_id", "suite_id"],
    basename="suites-test",
)
router.register(r"permissions/devicetypes", views.GroupDeviceTypePermissionViewSet)
router.register(r"permissions/devices", views.GroupDevicePermissionViewSet)
router.register(r"system", views.SystemViewSet, basename="system")
router.register(r"tags", views.TagViewSet)
router.register(r"workers", views.WorkerViewSet)
router.register(
    r"remote-artifact-tokens",
    views.RemoteArtifactTokenViewSet,
    basename="remote-artifact-tokens",
)
router.register(r"groups", views.GroupViewSet, basename="groups")
router.register(r"users", views.UserViewSet, basename="users")

# v0.3-only additions: dashboard aggregations, failure tags.
router.register(r"dashboard", views.DashboardViewSet, basename="dashboard")
router.register(
    r"failure-tags", views.JobFailureTagViewSet, basename="failure-tags"
)

# Singleton-style endpoints registered outside the router, if any.
extra_urls = []
