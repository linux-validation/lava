# Copyright (C) 2026 Qualcomm Technologies, Inc.
#
# Author: Matt Hart <matthart@qti.qualcomm.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from rest_framework import serializers

from lava_rest_app.v02.serializers import ChoiceField
from lava_scheduler_app.models import (
    Device,
    DeviceType,
    ExtendedUser,
    JobFailureTag,
    Notification,
    NotificationCallback,
    TestJob,
    Worker,
)


class TestJobPrioritySerializer(serializers.Serializer):
    priority = serializers.IntegerField(min_value=0, max_value=100)


class TestJobAnnotationSerializer(serializers.ModelSerializer):
    failure_tags = serializers.PrimaryKeyRelatedField(
        queryset=JobFailureTag.objects.all(), many=True, required=False
    )
    failure_comment = serializers.CharField(allow_blank=True, required=False)

    class Meta:
        model = TestJob
        fields = ("failure_tags", "failure_comment")


class TestJobFavoriteSerializer(serializers.Serializer):
    is_favorite = serializers.BooleanField(read_only=True)


class NotificationCallbackSerializer(serializers.ModelSerializer):
    method = ChoiceField(choices=NotificationCallback.METHOD_CHOICES)
    dataset = ChoiceField(choices=NotificationCallback.DATASET_CHOICES)
    content_type = ChoiceField(choices=NotificationCallback.CONTENT_TYPE_CHOICES)
    test_job = serializers.PrimaryKeyRelatedField(
        source="notification.test_job", read_only=True
    )

    class Meta:
        model = NotificationCallback
        fields = (
            "id",
            "notification",
            "test_job",
            "url",
            "method",
            "token",
            "header",
            "dataset",
            "content_type",
        )
        read_only_fields = ("id", "test_job")


class UserSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtendedUser
        fields = ("irc_handle", "irc_server", "table_length")


class QueueItemSerializer(serializers.ModelSerializer):
    submitter = serializers.CharField(source="submitter.username", read_only=True)
    requested_device_type = serializers.CharField(
        source="requested_device_type.name", read_only=True, allow_null=True
    )
    priority = serializers.IntegerField()
    state = serializers.CharField(source="get_state_display", read_only=True)
    queue_position = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = TestJob
        fields = (
            "id",
            "description",
            "submitter",
            "requested_device_type",
            "priority",
            "state",
            "submit_time",
            "health_check",
            "queue_position",
        )


class RunningDeviceTypeSerializer(serializers.Serializer):
    name = serializers.CharField()
    running_jobs = serializers.IntegerField(allow_null=True)
    running_devices = serializers.IntegerField(allow_null=True)
    reserved_devices = serializers.IntegerField(allow_null=True)


class LabHealthDeviceSerializer(serializers.ModelSerializer):
    device_type = serializers.CharField(source="device_type.name", read_only=True)
    worker_host = serializers.CharField(
        source="worker_host.hostname", read_only=True, allow_null=True
    )
    state = serializers.CharField(source="get_state_display", read_only=True)
    health = serializers.CharField(source="get_health_display", read_only=True)
    last_health_report_job = serializers.PrimaryKeyRelatedField(
        read_only=True, allow_null=True
    )

    class Meta:
        model = Device
        fields = (
            "hostname",
            "device_type",
            "worker_host",
            "state",
            "health",
            "last_health_report_job",
        )


class JobFailureTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobFailureTag
        fields = ("id", "name", "description")
