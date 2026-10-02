# Regress Stack

Welcome to **Regress Stack**! Regress Stack is a straightforward Ubuntu OpenStack package configurator. It is designed to simplify the process of setting up an OpenStack environment for testing purposes. With Regress Stack, you can easily configure OpenStack packages on a single node and run basic smoke tests to verify the functionality of the packages.

## Getting Started

To get started with Regress Stack, follow these simple steps:

1. **Clone the Repository**:

   ```bash
   git clone --recursive https://github.com/canonical/regress-stack.git
   cd regress-stack
   ```

2. **Install pre-commit**:

   ```bash
   uvx pre-commit install
   ```

3. **Install Dependencies**:

   ```bash
   sudo apt update
   sudo apt install dpkg-dev python3-dev python-apt-dev
   sudo apt install $(uv run regress-stack packages)
   uv sync
   ```

4. **Run Tests**:

   ```bash
   uv run py.test
   ```

5. **Run the Regress Stack**:

   ```bash
   sudo uv run regress-stack setup
   sudo uv run regress-stack test
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

## Contributing

We welcome contributions from the community! If you have ideas for new features or improvements, feel free to open an issue or submit a pull request.

## License

This project is licensed under the GNU General Public License v3.0 only. See the [LICENSE](LICENSE) file for details.

Happy Testing!
