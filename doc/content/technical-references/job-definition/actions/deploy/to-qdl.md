# QDL

The `qdl` deployment action downloads flat build tarball for Qualcomm devices
It is possible to add overlay to one of the partition images in the tarball.
QDL deployment is required to perform boot to qdl and flash the tarball contents to the board.

```yaml
- deploy:
    rootfs_image: rootfs.img
    overlay_path: /home
    qcomflash:
      url: ...
    to: qdl
```

## qcomflash

The `qcomflash` block specifies the location of the tarball to be downloaded.
It uses usual [download syntax](./index.md#artifacts).
The tarball should not be decompressed by download action.
It is assumed that the archive is compressed.

## rootfs_image

This parameter points to a partition image where the LAVA overlay should be added.
The value should be a path relative to the main directory in the tarball.

## overlay_path

This parameter names the path inside the [rootfs_image](#rootfs_image) where the LAVA overlay should be added. Value should be in line with `lava_test_results_dir` defined in the job context.
