# QDL

The qdl deployment action downloads a flat build tarball to Qualcomm devices
using [qdl](https://github.com/linux-msm/qdl).
It is possible to add overlays, including the LAVA overlay, to one of the
partition images within the tarball.
QDL deployment is required to boot into QDL mode and
flash the contents of the tarball onto the board, see the
[qdl boot method](../boot/method-qdl.md).

```yaml
- deploy:
    to: qdl
    rootfs_image: rootfs.img
    qcomflash:
      url: https://example.com/build.qcomflash.tar.gz
      format: ext4
      overlays:
        lava: true
```

## qcomflash

This parameter represents the tarball containing the build to be flashed to the
device. It uses the usual [download syntax](./index.md#artifacts).

The tarball is extracted into the deployment directory before flashing. The
[qdl boot method](../boot/method-qdl.md) then looks for the firehose programmer
and the rawprogram XML files relative to that directory.

The archive is assumed to be gzip compressed. Leave `compression` unset and
LAVA decompresses the tarball itself after downloading it; setting
`compression: gz` instead lets the download action decompress it on the fly.

### overlays

For applying overlays to the image, see [Overlays](./index.md#overlays).

Overlays are applied to the image named by [rootfs_image](#rootfs_image)
**inside** the tarball, not to the tarball itself, so `format` describes that
image rather than the archive. It is usually `ext4`, and `partition`, `sparse`
and `overlay_backend` may be used alongside it.

Every overlay is injected in a single pass over the image, in the order they
appear in the job definition.

```yaml
- deploy:
    to: qdl
    rootfs_image: core-image-base-qcs615-ride/rootfs.img
    qcomflash:
      url: https://example.com/build.qcomflash.tar.gz
      format: ext4
      overlays:
        lava: true
        modules:
          url: https://example.com/modules.tar.xz
          compression: xz
          format: tar
          path: /
        wifi-conf:
          url: https://example.com/wpa_supplicant.conf
          format: file
          path: /etc/wpa_supplicant.conf
```

The [LAVA overlay](./index.md#lava-overlay) is required when the job definition
contains tests.

The LAVA overlay tarball is unpacked at the root (`/`) of the filesystem
contained in `rootfs_image`, so its contents land in `lava_test_results_dir`
relative to that filesystem root. `lava_test_results_dir` defaults to
`/lava-<job_id>` for most deployments and can be changed through the
[job context](../../job.md#context), but the same value is also the path the
test shell scripts use on the booted device, so both ends move together and it
cannot be used to unpack the overlay somewhere else.

This matters for images where the filesystem in `rootfs_image` is not the
filesystem root seen after boot, for example OSTree based images, where the
booted deployment lives further down the tree. There the overlay is written
outside of the booted root, the test shell scripts do not find it at
`lava_test_results_dir` and the test job fails.

### apply-overlay

This is an older way of adding only the LAVA overlay to
[rootfs_image](#rootfs_image), kept for compatibility. It needs no `format`,
and treats the image as a whole filesystem rather than a partitioned image. The
deploy-level `overlay_backend` still applies.

```yaml
- deploy:
    to: qdl
    rootfs_image: rootfs.img
    qcomflash:
      url: https://example.com/build.qcomflash.tar.gz
      apply-overlay: true
```

Prefer `overlays: {lava: true}`, which additionally allows overlays of your
own. The two cannot be combined: setting `apply-overlay` together with
`overlays` is a job validation error, as both would write the LAVA overlay into
the same image.

## rootfs_image

This parameter points to the partition image within the tarball that overlays
are added to. The value should be a path relative to the main directory in the
tarball. It defaults to `rootfs.img`, and is only used when overlays are
applied.

## uniquify

Set to `true` to download the tarball into a sub-directory named after the
artifact, keeping the path unique. Defaults to `false`.
