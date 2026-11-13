# Regress Stack

Welcome to **Regress Stack**! Regress Stack is a straightforward Ubuntu OpenStack package configurator. It is designed to simplify the process of setting up an OpenStack environment for testing purposes. With Regress Stack, you can easily configure OpenStack packages on a single node and run basic smoke tests to verify the functionality of the packages.

## Minimum Requirements

| Resource | Minimum                                                    |
|----------|------------------------------------------------------------|
| OS       | Ubuntu (same release as the OpenStack packages under test) |
| CPU      | 4 vCPUs                                                    |
| RAM      | 8 GB                                                       |
| Disk     | 50 GB                                                      |
| Swap     | 4 GB (required if mem is <= 8GG — see below)               |

> **Swap is required if mem is <= 8GG.** The `discover-tempest-config` step downloads and processes
> cloud images in memory. Without swap, the kernel OOM-killer will terminate it
> with `SIGKILL`. Set up swap before running `regress-stack test`:
>
> ```bash
> sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
> sudo mkswap /swapfile && sudo swapon /swapfile
> echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
> ```

## Getting Started

To get started with Regress Stack, follow these simple steps:

1. **Clone the Repository**:

   ```bash
   git clone --recursive https://github.com/canonical/regress-stack.git
   cd regress-stack
   ```

2. **Install `uv`** (if not already installed):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   export PATH=$PATH:$HOME/.local/bin
   ```

3. **Install Dependencies**:

   ```bash
   sudo apt update
   sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq \
     -o Dpkg::Options::=--force-confdef \
     -o Dpkg::Options::=--force-confold \
     $(uv run regress-stack packages)
   ```

5. **Run the Regress Stack**:

   Both `setup` and `test` modify system configuration and must be run as root:

   ```bash
   sudo PATH=$PATH:/home/ubuntu/.local/bin uv run regress-stack setup

   # To run the tests, use:
   sudo PATH=$PATH:/home/ubuntu/.local/bin uv run regress-stack test
   ```

   After setup completes, credentials for the admin user are written to `~/auth.rc`
   (under root's home when using `sudo`). Source them to use the OpenStack CLI:

   ```bash
   source ~/auth.rc
   openstack service list
   ```

6. **Run Unit Tests** (optional, for contributors):

   ```bash
   uv run py.test
   ```

Regress Stack currently supports the following OpenStack modules:

- **Ceph**: `ceph-mgr`, `ceph-mon`, `ceph-osd`, `ceph-volume`
- **Cinder**: `cinder-api`, `cinder-scheduler`, `cinder-volume`
- **Glance**: `glance-api`
- **Heat**: `heat-api`, `heat-api-cfn`, `heat-engine`
- **Barbican**: `barbican-api`, `barbican-keystone-listener`, `barbican-worker`
- **Keystone**: `keystone`, `apache2`, `libapache2-mod-wsgi-py3`
- **Magnum**: `magnum-api`, `magnum-conductor`
- **Neutron**: `neutron-server`, `neutron-ovn-metadata-agent`
- **Nova**: `nova-api`, `nova-conductor`, `nova-scheduler`, `nova-compute`, `nova-spiceproxy`, `spice-html5`
- **OVN**: `ovn-central`, `openvswitch-switch`, `ovn-host`
- **Placement**: `placement-api`
- **Watcher**: `watcher-api`, `watcher-decision-engine`, `watcher-applier`

The following modules are available on [Sunbeam](https://github.com/canonical/snap-openstack) but are not currently supported by Regress Stack:

- **Horizon**
- **Ironic**
- **Masakari**
- **Octavia**
- **Manila**
- **AODH**
- **Ceilometer**
- **Gnocchi**

## PPA priority in `spread` tests

When running `spread`, set `APT_PPA_PRIORITY` to control the priority of packages
from the PPA selected by `APT_PPAS` when generating the configuration with
`boot.sh`. For example, test UCA Hibiscus staging on Resolute:

```bash
APT_PPA_PRIORITY=500 RELEASES=resolute APT_POCKETS=true APT_PPAS=ppa:ubuntu-cloud-archive/hibiscus-staging ./boot.sh
spread
```

Packages are installed from different sources depending on the [priority values](https://manpages.ubuntu.com/manpages/focal/man5/apt_preferences.5.html). Typical values are:

- `1001`: Force PPA versions over Ubuntu packages, including downgrades.
- `600`: Prefer the PPA over proposed without forcing downgrades of installed packages.
- `500`: Give the PPA the same priority as `proposed` and select the newest version.
- `400`: Prefer Ubuntu packages and use the PPA for packages unavailable in Ubuntu.

## CI Doctor (GitHub Agentic Workflow)

Failed `Pull Request` and `Functional Tests` runs on `main` are investigated by
the [CI Doctor](https://github.com/githubnext/agentics/blob/main/workflows/ci-doctor.md)
[agentic workflow](https://github.github.com/gh-aw/). It reads the failed job
logs, correlates them with the commit under test and opens (or comments on) a
`[CI failure]` issue with the likely root cause and suggested fixes.

Inference uses Anthropic Claude (`anthropic/claude-sonnet-5.5`) through
[OpenRouter](https://openrouter.ai/). To enable it, add an `OPENROUTER_API_KEY`
repository secret.

The workflow is defined in `.github/workflows/ci-doctor.md`. After editing it,
regenerate the lock file with the [`gh aw`](https://github.com/github/gh-aw)
extension and commit both files:

```bash
gh extension install github/gh-aw
gh aw compile ci-doctor
```

## Contributing

We welcome contributions from the community! If you have ideas for new features or improvements, feel free to open an issue or submit a pull request.

Before submitting code changes, install the pre-commit hooks:

```bash
uvx pre-commit install
```

## License

This project is licensed under the GNU General Public License v3.0 only. See the [LICENSE](LICENSE) file for details.

Happy Testing!
