# QDL

The `qdl` boot method allows to flash Qualcomm devices using [qdl](https://github.com/linux-msm/qdl) tool.

```
- boot:
    method: qdl
    firehose_program: "prog_firehose_ddr.elf"
    rawprogram: "rawprogram*.xml"
    patch: "patch*.xml"
    storage: "emmc"
    timeout:
      minutes: 5
```

## Installation

LAVA supports running `qdl` directly on the worker host or from Docker container.
In both cases LAVA administrators have to make sure `qdl` is installed as it's
not a direct LAVA dependency.

The latest release is available at
<https://github.com/linux-msm/qdl/releases>

## Device configuration

## qdl parameters

Some of the `qdl` parameters must be provided in the job definition.

### firehose_program

Since different Qualcomm devices use different `firehose` protocol implementations
user must specify the name of the `firehose` program to be used by `qdl.
This name is relative to the top tarball directory.
See [deploy-to-qdl](../deploy/to-qdl.md) for more details.

### rawprogram

List of `rawprogram` files used by `qdl`. The names should be delimited by white space
Paths are relative to top tarball directory.
See [deploy-to-qdl](../deploy/to-qdl.md) for more details.

### patch

List of `patch` files used by `qdl`. The names should be delimited by white space
Paths are relative to top tarball directory.
See [deploy-to-qdl](../deploy/to-qdl.md) for more details.

### storage

`qdl` allows to write data to various storage devices: mmc, ufs, spinor, etc.
See `qdl` [documentation](https://github.com/linux-msm/qdl/blob/master/README.md) for more details.
