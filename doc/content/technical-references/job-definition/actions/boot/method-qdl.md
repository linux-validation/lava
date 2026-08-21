# QDL

The `qdl` boot method allows to flash Qualcomm devices using the [qdl](https://github.com/linux-msm/qdl) tool.

```
- boot:
    method: qdl
    firehose_program: "prog_firehose_ddr.elf"
    rawprogram: "rawprogram*.xml"
    patch: "patch*.xml"
    path: "path-to-dir-inside-tarball"
    storage: "emmc"
    timeout:
      minutes: 5
```

## Installation

LAVA supports running `qdl` directly on the worker host or from a Docker container.
In both cases, LAVA administrators have to make sure `qdl` is installed on the worker.

The latest release is available at [https://github.com/linux-msm/qdl/releases](https://github.com/linux-msm/qdl/releases).

## Device configuration

## qdl parameters

Some of the `qdl` parameters must be provided in the job definition.

### firehose_program

Since each Qualcomm devices uses a different `firehose` protocol implementation,
the user must specify the filename of the `firehose` program to be used by `qdl`.
This filename is relative to the top tarball directory.
See [deploy-to-qdl](../deploy/to-qdl.md) for more details.

### rawprogram

List of `rawprogram` files to be used by `qdl`. The filenames should be delimited by whitespace
and should be specified relative to the root of the tarball defined in `qcomflash`.
See [deploy-to-qdl](../deploy/to-qdl.md) for more details.

### patch

List of `patch` files used by `qdl`. The filenames should be delimited by whitespace
and should be specified relative to the root of the tarball defined in `qcomflash`.
See [deploy-to-qdl](../deploy/to-qdl.md) for more details.

### storage

Storage device for `qdl` to write data to. Supported values include `emmc`, `ufs`, `spinor`, etc.
See [qdl documentation](https://github.com/linux-msm/qdl/blob/master/README.md) for more details.

### path

Path inside the downloaded tarball containing the `rawprogram` and `patch` files.
The paths referenced by `rawprogram` and `patch` files are relative, so `qdl` must be ran from this directory.

### debug

Set to `true` to run `qdl` with `--debug`, which makes it log the firehose
exchange with the board. Defaults to `false`.

## Ramdump

When a Qualcomm board crashes it can re-enumerate on USB in EDL crashdump mode,
holding a dump of DDR and the other memory regions. LAVA can collect that dump
so the crash can be analysed after the job has finished.

```yaml
- boot:
    method: qdl
    firehose_program: "prog_firehose_ddr.elf"
    rawprogram: "rawprogram0.xml"
    ramdump: true
    ramdump_segments:
    - OCIMEM.BIN
    - DDRCS0.BIN
    ramdump_timeout: 1800
    ramdump_compression: zstd
    timeout:
      minutes: 5
```

LAVA looks for the crashed board once, while the job is being torn down, so
that a board which crashes at any point during the job is still caught. That
happens whether the job passed, failed or was cancelled.

A job captures at most one dump, however many qdl boot actions it contains:
they all name the dump after the job, so a second would overwrite the first.

If the board is not in crashdump mode nothing happens and no result is
recorded, so `ramdump: true` can be left on permanently. Detection matches the
device's `usb_vendor_id` against the EDL crashdump product ids `900e`, `901d`
and `90db`. The normal firehose flashing mode, `9008`, is deliberately not one
of them. A device that dumps under a different id can add it with
`qdl_ramdump_product_id` in its device dictionary.

A captured dump is tarred, compressed and handed to the
[publish command](#publishing) configured for the worker, which is what makes
it outlive the job. LAVA then records a `ramdump` test case in the `lava`
suite, whose `ramdump` extra field holds the name of the published object and,
if the publish command reported one, whose `ramdump_url` field links to it.

Nothing is captured on a worker with no publish command configured, since
there would be nowhere for the dump to go.

### ramdump

Set to `true` to capture a dump when the board is found in crashdump mode.
Defaults to `false`.

### ramdump_segments

The dump regions to capture, passed to `qdl` as a comma separated list. The
available names depend on the SoC. When omitted, `qdl` captures every region
the board offers, which is the largest and slowest option.

### ramdump_timeout

How long the whole thing may take, in seconds. Defaults to `900`.

This is one budget covering all of it: dumping the board, compressing what
came out and handing that to the publish command. A full DDR dump over USB
routinely takes several minutes and the upload after it can take longer still,
far more than the budget the rest of the job teardown runs under, so it is all
given a window of its own. Raise it for boards with a lot of DDR, or where the
dump has a long way to travel.

Whatever it runs out of time in the middle of, the `ramdump` test case fails
and the job carries on being torn down.

### ramdump_compression

How to compress the dump tarball before publishing: `gz` (the default), `xz`,
`bz2` or `zstd`. Dumps are large and compress well, so this makes a
considerable difference to how much is transferred and stored.

### Publishing

Where ramdumps should be kept is site specific, so LAVA does not deliver them
itself. Instead the worker's
[dispatcher configuration](../../../configuration/dispatcher.md) provides a
single `ramdump_publish_command`:

```yaml
ramdump_publish_command: /usr/local/bin/publish-ramdump
```

LAVA runs it with the dump described in the environment:

* `LAVA_RAMDUMP_FILE`: full path of the compressed tarball on the worker
* `LAVA_RAMDUMP_NAME`: its filename, `ramdump-<job_id>.tar.<compression>`
* `LAVA_JOB_ID`: the job that produced it

The command may print the URL of the uploaded dump as the **last line** of its
output. LAVA records it as the `ramdump_url` field of the `ramdump` result and
logs it, so an engineer can go straight from the job to the dump:

```sh
#!/bin/sh
# Nothing but the URL may reach stdout or stderr, so send the uploader's own
# output elsewhere: /dev/null, or a log file of your own to debug with.
# A failed upload prints no URL.
if aws s3 cp "$LAVA_RAMDUMP_FILE" \
        "s3://dumps/$LAVA_JOB_ID/$LAVA_RAMDUMP_NAME" >/dev/null 2>&1
then
    echo "https://dumps.example.com/$LAVA_JOB_ID/$LAVA_RAMDUMP_NAME"
fi
```

Printing a URL is optional; without one the dump is simply recorded by name.
Any line is accepted as long as it has the shape of a URL, whatever the scheme,
so `s3://`, `scp://` or `file://` work as well as `https://`.

Two rules follow from how that output is read:

* LAVA merges the command's **stderr into its stdout**, so the URL has to be
  the last thing written to either stream. A warning printed to stderr after
  the URL replaces it, and the dump is recorded without a link.
* LAVA does **not** read the command's exit status. A command that fails must
  print no URL, or the result will claim the dump was published and link to
  something that was never uploaded.

Nothing the command prints is written to the job log, so a token or a signed
URL used along the way stays out of a publicly visible job. Only the last line
is looked at, so a chatty uploader cannot put arbitrary text into the result.

A worker with no `ramdump_publish_command` configured captures nothing at all.
A dump is written to the job's temporary directory, which LAVA deletes as soon
as teardown finishes, so with nowhere to deliver it there is nothing to be
gained from spending several minutes of that teardown making one. LAVA says so
in the job log and records no result, so `ramdump: true` costs nothing on a
worker that cannot deliver dumps.
