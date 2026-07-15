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

LAVA supports running `qdl` directly on the worker host or inside a Docker
container. See [docker](#docker) for running with docker. When running directly,
`qdl` must be installed manually on the LAVA worker since it is not provided as a
dependency by LAVA. When running with docker, the `qdl` binary must be present
inside the container image instead.

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

## docker

`qdl` can run inside a Docker container instead of on the worker host. The
container is defined entirely in the test job by adding a `docker` block to the
boot action. When no `docker` block is present, `qdl` runs natively on the
worker. The container image must contain the `qdl` binary.

```yaml
- deploy:
    to: qdl
    qcomflash:
      url: 'https://example.com/qcomflash.tar.gz'
    timeout:
      minutes: 20

- boot:
    method: qdl
    docker:
      image: qualcomm/qdl:latest
    firehose_program: "prog_firehose_ddr.elf"
    rawprogram: "rawprogram*.xml"
    patch: "patch*.xml"
    path: "."
    storage: "ufs"
    timeout:
      minutes: 5
```

### image

The Docker image name. The image must contain the `qdl` binary.

### local

Optional. If `true`, LAVA will use the image if it already exists locally on the
worker without pulling from a registry.

### remote_options

Optional. When the device is connected to a machine other than the LAVA worker,
set `remote_options` to the Docker client options needed to reach the remote
Docker daemon:

```yaml
- boot:
    method: qdl
    docker:
      image: qualcomm/qdl:latest
      remote_options: "--tlsverify --tlscacert=/certs/ca.pem --tlscert=/certs/cert.pem --tlskey=/certs/key.pem -H 10.192.244.5:2376"
    firehose_program: "prog_firehose_ddr.elf"
    rawprogram: "rawprogram*.xml"
    patch: "patch*.xml"
    path: "."
    storage: "ufs"
    timeout:
      minutes: 5
```

With `remote_options` set, the flashing files (downloaded on the worker) are made
available to the remote container over NFS, so the worker must export the
dispatcher temporary directory and the container image must provide `mount` and
NFS client support.
