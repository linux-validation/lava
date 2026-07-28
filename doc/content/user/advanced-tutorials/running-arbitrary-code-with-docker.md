# Running arbitrary code with docker

## Introduction

Testing in LAVA will often require running arbitrary code on the LAVA
dispatcher. Of course, no lab admin would ever allow users running arbitrary
code on their systems, so we need a solution to have users run their arbitrary
code in isolated containers.

This document describes how to use docker to cover the use cases were users
need to run arbitrary code on the lava dispatcher.

## Use case 1: Fastboot deploy from a docker container

Deploying and booting fastboot devices using docker allows you to provide your
own image with a pre-installed fastboot binary, making test jobs faster. To do
this, you just need to add the `docker` section to the fastboot deploy and boot
actions:

```yaml
actions:
# ...
    - deploy:
        to: fastboot
        docker:
            image: my-fastboot-image
        timeout:
            minutes: 15
        images:
            boot:
                url: http://example.com/images/aosp/hikey/boot.img
                reboot: hard-reset
# ...
    - boot:
        method: fastboot
        docker:
            image: my-fastboot-image
        prompts:
            - 'healthd: No battery devices found'
            - 'hikey: '
            - 'console:'
        timeout:
            minutes: 15
```

## Use case 2: Manipulating downloaded images

Some use cases involve downloading different build images and combining them
somehow. Examples include but are not limited to:

* Injecting kernel modules into a rootfs
* Downloading separate kernel/modules/rootfs and combining them in a single
  image for flashing.

This can be achieved using the "**downloads**" deploy method (note
"**downloads**", plural; "**download**"), plus postprocessing instructions:

```yaml
actions:
# ...
    - deploy:
        to: downloads
        images:
            # [...]
            kernel:
                url: http://images.com/.../Image
            modules:
                url: http://images.com/.../modules.tar.xz
            rootfs:
                url: http://images.com/.../rootfs.ext4.gz
                apply-overlay: true
        postprocess:
            docker:
                image: my-kir-image
                steps:
                    - /kir/lava/board_setup.sh hi6220-hikey-r2
```

This will cause all the specified images to be downloaded, and then a docker container
running the specified will be executed.

* The container will have the download directory as the current directory.
    * i.e. the downloaded images will be present in the current directory.
* The steps listed in `steps:` will be executed in order
* Any file modified or created by the steps is left around for later usage.

After the postprocessing fininshes, the resulting images can be used by
specifying their location using the `downloads://` pseudo-URL in a subsequent
deploy action:

```yaml
# ...
    - deploy:
        to: fastboot
        images:
            system:
                rootfs: downloads://rootfs.img
            boot:
                url: downloads://boot.img
```

Those pseudo-URLs are relative to the download directory, from where the
container was executed.

## Use case 3: Running tests from the docker container

To run tests from a docker container, you just need to add a `docker` section
to the well-known LAVA test shell action:

```yaml
# ...
    - test:
        docker:
            image: my-adb-image
        timeout:
        minutes: 5
        definitions:
            - repository:
                # [...]
                from: inline
                path: inline-smoke-test
                name: docker-test
# ...
```

The specified test definitions will be executed inside a container running the
specified image, and the following applies:

* The USB connection to the device is shared with the container, so that you
  can run `adb` and have it connect to the device.
    * For example this can be used in AOSP jobs to run CTS/VTS against the
      device.
* The device connection settings are exposed to the tests running in the
  container via environment variables. For example, assume the given connection
  commands in the device configuration:
    ```jinja
    {% set connection_list = ['uart0', 'uart1'] %}
    {% set connection_commands = {
        'uart0': 'telnet localhost 4002',
        'uart1': 'telnet 192.168.1.200 8001',
        }
    %}
    {% set connection_tags = {'uart1': ['primary', 'telnet']} %}
    ```

    These connection settings will be exported to the container environment as:

    ```shell
    LAVA_CONNECTION_COMMAND='telnet 192.168.1.200 8001'
    LAVA_CONNECTION_COMMAND_UART0='telnet localhost 4002'
    LAVA_CONNECTION_COMMAND_UART1='telnet 192.168.1.200 8001'
    ```

    Of course, for this to work the network addresses used in the configuration
    need to be resolvable from inside the docker container. This requires
    coordination with the lab administration.
* The device power control commands are also exposed in the following
  environment variables: `LAVA_HARD_RESET_COMMAND`, `LAVA_POWER_ON_COMMAND`,
  and `LAVA_POWER_OFF_COMMAND`.

  The same caveat as with the connection commands: any network addresses used
  in such commands need to be accessible from inside the container.

  Note that each of these operations can actually require more than one
  command, in which case the corresponding environment variable will have the
  multiple commands with `&&` between them. Because of this, the safest way to
  run the commands is passing the entire contents of the variable as a single
  argument to `sh -c`, like this:

  ```bash
  sh -c "${LAVA_HARD_RESET_COMMAND}"
  ```

#### Running device commands on the worker

Exporting the power control commands as environment variables only works when
the commands can run unchanged inside the container: the tools they invoke must
be installed in the image, and any addresses they use must be reachable from the
container's network. That is frequently not the case - power control is often
done with a host binary talking to a local USB relay.

Instead, the test can ask the dispatcher to run the command on the worker, where
those tools and that network already are. Opt in with `device_commands`:

```yaml
# ...
    - test:
        docker:
            image: adb-fastboot
        device_commands: true
# ...
```

The test then calls `lava-device-command`, or one of the `lava-power-on`,
`lava-power-off` and `lava-hard-reset` shortcuts:

```yaml
            run:
                steps:
                    - lava-hard-reset
                    - lava-device-command usb_c_off
```

The commands that can be asked for are the builtin ones (`power_on`,
`power_off`, `hard_reset`, `pre_power_command`, `pre_os_command`, ...) and the
device's user commands. Asking for one the device does not define fails the call
rather than the job, so the same job definition works across devices that do not
all provide the same commands.

The call blocks until the command has finished on the worker and exits with its
return code.

Commands are exchanged with the dispatcher over the test shell's own stdin and
stdout, so only its foreground process can ask for one. A test that wants device
commands from somewhere else - a background process, or a session that arrived
over the network - has to relay them through something running in that
foreground.

Note that power cycling a device makes it re-enumerate over USB. Its device
nodes are shared with the container again when that happens, but only once udev
has seen the device come back, so a test that power cycles the device has to
wait for the nodes it needs to reappear before using them.

#### Using password protected docker images

To pull images from a password protected registry add a login section
with the registry domain name, username and password.

```yaml
# ...
    - test:
        docker:
            image: example.com/my-adb-image
                login:
                    registry: example.com
                    user: foobar
                    password: my_password
# ...
```

**Warning**: The pulled image will be cached on the local daemon and will
be available to other jobs running on same worker.

## See also

* LAVA release notes:
    * [2020.01](https://gitlab.com/lava/lava/-/wikis/releases/2020.01)
    * [2020.02](https://gitlab.com/lava/lava/-/wikis/releases/2020.02)
    * [2020.04](https://gitlab.com/lava/lava/-/wikis/releases/2020.04)
* [Improved Android Testing in LAVA with Docker](https://connect.linaro.org/resources/ltd20/ltd20-304/). Talk at Linaro Tech Days 2020.
