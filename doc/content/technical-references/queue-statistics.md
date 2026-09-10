# Queue statistics

LAVA records how long test jobs wait for a device and how long they occupy it,
per device-type. The figures are shown on the device-type page and are
available from the REST API.

## Why the queue is sampled

Queue length is an instantaneous quantity: a job is in the queue between its
`submit_time` and its `start_time`, and not outside that interval. There is no
column to read it from, and reconstructing a history of it means scanning every
job submitted in the window.

So [lava-scheduler](./services/lava-scheduler.md) samples it instead. Every
`QUEUE_SNAPSHOT_INTERVAL` seconds it writes one row per device-type recording
the state of the queue at that moment, and the device-type page and the API
read those rows back.

The consequence worth knowing is that a burst that starts and clears between
two samples leaves no trace in the queue length, so peaks are floors rather
than true maxima. The wait times of the jobs involved are still recorded, since
`started_jobs` counts everything that started since the previous sample rather
than what was visible at one instant. Sampling more often narrows the gap at a
cost of one row per device-type per interval.

## What a snapshot contains

| Field | Meaning |
| ----- | ------- |
| `device_type` | The device-type this sample is for. |
| `timestamp` | When the sample was taken. |
| `queued_jobs` | Jobs waiting for a device of this type at that instant. |
| `running_jobs` | Jobs reserved for or running on this device-type. |
| `available_devices` | Devices that are idle, healthy, and on an online worker. |
| `started_jobs` | Jobs that started since the previous sample. |
| `average_wait_time` | Mean `start_time - submit_time` over `started_jobs`. |
| `finished_jobs` | Jobs that finished since the previous sample. |
| `average_duration` | Mean `end_time - start_time` over `finished_jobs`. |

The two averages are stored beside the count they were computed over. That is
what makes them safe to combine: averaging a range of snapshots weighted by
`started_jobs` gives the true average wait over that range, whereas averaging
the averages would let a quiet sample covering one slow job count as much as a
busy one covering fifty fast ones.

Each sample covers the period since the previous one, taken from the last row
in the database rather than from the clock. Consecutive windows therefore abut
exactly: no job is counted twice and none is missed, even if the scheduler was
restarted or its loop stalled in between.

!!! info "Jobs that never start"
    A cancelled job, or one still queued when the window ends, contributes to
    `queued_jobs` for as long as it waited but never appears in `started_jobs`
    or in the wait average.

## Configuration

Set these in one of the [LAVA settings
files](../admin/basic-tutorials/instance/configure.md#configuration-files):

```yaml
QUEUE_SNAPSHOT_INTERVAL: 300
QUEUE_SNAPSHOT_RETENTION_DAYS: 90
QUEUE_SNAPSHOT_PRUNE_INTERVAL: 86400
QUEUE_STATS_WINDOW_DAYS: 7
```

| Setting | Default | Meaning |
| ------- | ------- | ------- |
| `QUEUE_SNAPSHOT_INTERVAL` | `300` | Seconds between samples. Set to `0` to stop recording. |
| `QUEUE_SNAPSHOT_RETENTION_DAYS` | `90` | How long samples are kept. |
| `QUEUE_SNAPSHOT_PRUNE_INTERVAL` | `86400` | Seconds between sweeps of expired samples. Only affects how promptly the delete runs, not what is kept. |
| `QUEUE_STATS_WINDOW_DAYS` | `7` | Window the device-type page reports over, and the default for the API. |

Pruning happens inside the scheduler, so an instance that does not run
`lava-scheduler` neither records nor expires samples.

## Retrieving the statistics

The aggregate for one device-type:

```shell
curl https://validation.linaro.org/api/v0.2/devicetypes/qemu/statistics/
```

```json
{
    "device_type": "qemu",
    "days": 7,
    "snapshots": 1008,
    "average_wait_time": "00:21:18.610065",
    "average_wait_jobs": 1325,
    "average_duration": "00:07:36.826149",
    "average_duration_jobs": 1325,
    "utilisation": 25.297619047619047,
    "queued_jobs": 21,
    "running_jobs": 3,
    "available_devices": 2,
    "last_sample": "2026-09-10T17:43:42.032538Z"
}
```

`average_wait_time` and `average_duration` are combined across every snapshot
in the window, weighted as described above; `average_wait_jobs` and
`average_duration_jobs` are the populations behind them. `queued_jobs`,
`running_jobs` and `available_devices` come from the most recent sample, not
from the window, and are `null` when there is none.

`utilisation` is the share of usable devices that were busy, averaged over the
samples. It is measured against the devices the scheduler could actually have
used at each sample — busy plus idle-and-healthy — so a retired device, or one
on an offline worker, does not read as spare capacity. It is `null` when no
device of that type was usable at any sample.

The window defaults to `QUEUE_STATS_WINDOW_DAYS` and can be changed with
`days`, up to the retention period:

```shell
curl https://validation.linaro.org/api/v0.2/devicetypes/qemu/statistics/?days=30
```

A `days` value that is not an integer, or is outside `1` to
`QUEUE_SNAPSHOT_RETENTION_DAYS`, is rejected with `400` rather than silently
returning an empty window.

## Retrieving the samples

The rows behind those figures are available on their own, newest first and
paginated:

```shell
curl https://validation.linaro.org/api/v0.2/devicetypes/qemu/snapshots/
```

```json
{
    "device_type": "qemu",
    "timestamp": "2026-09-10T17:43:42.032538Z",
    "queued_jobs": 21,
    "running_jobs": 3,
    "available_devices": 2,
    "started_jobs": 4,
    "average_wait_time": "00:34:23.883669",
    "finished_jobs": 3,
    "average_duration": "00:02:51.027263"
}
```

Use this to plot the queue over time, or to look at a specific incident rather
than at an average.

The list can be narrowed by timestamp and by the counts, and ordered by
timestamp:

```shell
# One day of samples, oldest first
curl "https://validation.linaro.org/api/v0.2/devicetypes/qemu/snapshots/?timestamp__gte=2026-09-09T00:00:00Z&timestamp__lt=2026-09-10T00:00:00Z&ordering=timestamp"

# Only the samples where the queue was deeper than 20
curl "https://validation.linaro.org/api/v0.2/devicetypes/qemu/snapshots/?queued_jobs__gt=20"
```

!!! info "Visibility"
    Both endpoints are nested under a device-type and follow its
    [permissions](./authorization.md). A device-type the user is not allowed to
    see answers `404`, the same as `/devicetypes/<name>/` itself.

## On the device-type page

The device-type page reports the average wait, the average job duration and the
utilisation over `QUEUE_STATS_WINDOW_DAYS`, alongside a chart of the queue
length over the same window. Those are the same numbers the
`statistics/` endpoint returns, computed by the same code.
