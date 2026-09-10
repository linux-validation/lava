# Copyright (C) 2024 Linaro Limited
#
# Author: Igor Ponomarev <igor.ponomarev@collabora.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

from typing import Any


# Custom Job id converter
class JobIdConverter:
    # Multipart job uses the job_id.sub_id format
    # Example: 12345.9876
    # To preserve accuracy job id has to be string
    regex = r"[0-9]+|[0-9]+\.[0-9]+"

    def to_python(self, value: Any) -> Any:
        return value

    def to_url(self, value: Any) -> str:
        return str(value)


def register_job_id_converter() -> None:
    """
    Register JobIdConverter as "job_id", once.

    Several apps route on job ids and each has to make sure the converter
    exists before its urlpatterns are built. Converters are global and
    Django 5.1 turned re-registering one into a ValueError, so the
    registration has to be idempotent.
    """
    from django.urls import register_converter
    from django.urls.converters import get_converters

    if "job_id" in get_converters():
        return
    register_converter(JobIdConverter, "job_id")
