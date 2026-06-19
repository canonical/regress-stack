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
   git clone https://github.com/canonical/regress-stack.git
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
   sudo apt install dpkg-dev python3-dev python-apt-dev
   uv sync
   ```

4. **Install OpenStack Packages**:

   ```bash
   sudo apt install $(uv run regress-stack packages)
   ```

5. **Run the Regress Stack**:

   Both `setup` and `test` modify system configuration and must be run as root:

   ```bash
   sudo -s
   export PATH=$PATH:/home/ubuntu/.local/bin
   uv run regress-stack setup
   uv run regress-stack test
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
- **Keystone**: `keystone`, `apache2`, `libapache2-mod-wsgi-py3`
- **Magnum**: `magnum-api`, `magnum-conductor`
- **Neutron**: `neutron-server`, `neutron-ovn-metadata-agent`
- **Nova**: `nova-api`, `nova-conductor`, `nova-scheduler`, `nova-compute`, `nova-spiceproxy`, `spice-html5`
- **OVN**: `ovn-central`, `openvswitch-switch`, `ovn-host`
- **Placement**: `placement-api`

The following modules are available on [Sunbeam](https://github.com/canonical/snap-openstack) but are not currently supported by Regress Stack:

- **Horizon**
- **Ironic**
- **Masakari**
- **Octavia**
- **Watcher**
- **Manila**
- **Barbican**
- **AODH**
- **Ceilometer**
- **Gnocchi**

## Contributing

We welcome contributions from the community! If you have ideas for new features or improvements, feel free to open an issue or submit a pull request.

Before submitting code changes, install the pre-commit hooks:

```bash
uvx pre-commit install
```

## License

This project is licensed under the GNU General Public License v3.0 only. See the [LICENSE](LICENSE) file for details.

Happy Testing!
