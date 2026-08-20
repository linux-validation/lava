# LAVA worker

Manage local lava jobs for attached DUTs.

## Command line

Run `/usr/bin/lava-worker`.

## Service

The systemd service is called `lava-worker`.

## Dependencies

lava-worker should be able to:

* connect to [lava-server-gunicorn](./lava-server-gunicorn.md)

## Configuration

Daemon start options:

* `/etc/default/lava-worker`
* `/etc/lava-server/lava-worker`

## Reported capacity

Along with each ping, the worker tells the server how much capacity it has.
The figures appear on the worker's page in the web interface, in the workers
list, and in the REST and XML-RPC APIs:

* the kernel release, architecture, CPU model and core count
* the 1, 5 and 15 minute load averages
* total and available memory
* total and free space on the filesystem holding the job temporary directory,
  and the path of that directory
* how long the worker has been up, stored as the time it booted

They are a snapshot taken when the worker last pinged, not a history, so read
them next to the "Last ping" time. Nothing is stored for a worker running an
older version of LAVA, and a worker unable to read one figure still reports
the rest.

The disk figures describe the directory job files are written to, which is
`/var/lib/lava/dispatcher/tmp` unless the worker's
[configuration](../configuration/dispatcher.md) sets `dispatcher_download_dir`.
That setting reaches the worker with each job rather than with a ping, so a
worker that has not run a job since it started reports the default directory;
the first job corrects it. The directory being measured is shown next to the
figures, so it is always clear which filesystem they describe.

For a worker started by [lava-docker-worker](./lava-docker-worker.md) these
describe the host rather than the container: the job temporary directory is
bind mounted from the host, and the kernel, load average and memory are the
host's because the container shares them.

## Logs

The logs are stored in `/var/log/lava-dispatcher/lava-worker.log`

The log rotation is configured in `/etc/logrotate.d/lava-worker-log`.

## Security

TODO: should activate encryption
