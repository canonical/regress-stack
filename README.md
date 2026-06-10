# Regress Stack

Welcome to **Regress Stack**! Regress Stack is a straightforward Ubuntu OpenStack package configurator. It is designed to simplify the process of setting up an OpenStack environment for testing purposes. With Regress Stack, you can easily configure OpenStack packages on a single node and run basic smoke tests to verify the functionality of the packages.

## Getting Started

To get started with Regress Stack, follow these simple steps:

1. **Clone the Repository**:

   ```bash
   git clone https://github.com/canonical/regress-stack.git
   cd regress-stack
   ```

2. **Install pre-commit**:

   ```bash
   uvx pre-commit install
   ```

3. **Install Dependencies**:

   ```bash
   sudo apt install dpkg-dev python3-dev python-apt-dev
   uv sync
   ```

4. **Run Tests**:

   ```bash
   uv run py.test
   ```

5. **Run the Regress Stack**:

   ```bash
   uv run regress-stack setup
   uv run regress-stack test
   ```

Regress Stack currently supports the following OpenStack modules:

- **Ceph**: `ceph-mgr`, `ceph-mon`, `ceph-osd`, `ceph-volume`
- **Cinder**: `cinder-api`, `cinder-scheduler`, `cinder-volume`
- **Glance**: `glance-api`
- **Heat**: `heat-api`, `heat-api-cfn`, `heat-engine`
- **Ironic**: `ironic-api`, `ironic-conductor`
- **Keystone**: `keystone`, `apache2`, `libapache2-mod-wsgi-py3`
- **Magnum**: `magnum-api`, `magnum-conductor`
- **Neutron**: `neutron-server`, `neutron-ovn-metadata-agent`
- **Nova**: `nova-api`, `nova-conductor`, `nova-scheduler`, `nova-compute`, `nova-spiceproxy`, `spice-html5`
- **OVN**: `ovn-central`, `openvswitch-switch`, `ovn-host`
- **Placement**: `placement-api`

The following modules are available on [Sunbeam](https://github.com/canonical/snap-openstack) but are not currently supported by Regress Stack:

- **Horizon**
- **Masakari**
- **Octavia**
- **Watcher**
- **Manila**
- **Barbican**
- **AODH**
- **Ceilometer**
- **Gnocchi**

## Ironic Profiles

Ironic is enabled by default. The default profile is `fake-hardware`, which is
intended to behave like the other `regress-stack` package/regression lanes:

```bash
uv run regress-stack setup
uv run regress-stack test
```

Select a profile explicitly when you need different behavior:

```bash
IRONIC_PROFILE=fake-hardware uv run regress-stack setup
IRONIC_PROFILE=ipmi uv run regress-stack setup
```

Profile summary:

- `fake-hardware`
  - default
  - intended for package validation and regression detection
  - runs a curated `ironic_tempest_plugin.tests.api.admin` Tempest subset
  - lighter footprint
  - no nested libvirt guest
  - no `virtualbmc`
- `ipmi`
  - advanced profile
  - provisions a nested libvirt guest exposed through `virtualbmc`
  - uploads TinyIPA deploy images and drives one nested node to `available`
  - provides better realism for deploy-path, power-control, and networking
    issues

## Tempest Notes

Ubuntu's packaged `ironic-tempest-plugin` defaults many API tests to
`fake-hardware`, and the default `regress-stack` Ironic profile now matches
that expectation.

Practical guidance:

- the default `fake-hardware` profile is the preferred path for package and
  regression testing
- the default `fake-hardware` Tempest lane targets
  `ironic_tempest_plugin.tests.api.admin` with additional excludes for
  unsupported fake-hardware features
- the advanced `ipmi` profile is more realistic, but its Tempest coverage
  should still be treated as curated per release

## VM Requirements

Recommended minimums depend on the feature set you enable:

- Regress Stack with the default `fake-hardware` Ironic profile:
  4 vCPU, 8 GiB RAM, 40 GiB disk
- Regress Stack with the advanced `ipmi` Ironic profile:
  8 vCPU, 16 GiB RAM, 80 GiB disk

For the `ipmi` profile, nested virtualization support is
strongly recommended. Without `/dev/kvm`, the nested baremetal VM path is
still conceptually supported, but it will be much slower and more
failure-prone.

## Contributing

We welcome contributions from the community! If you have ideas for new features or improvements, feel free to open an issue or submit a pull request.

## License

This project is licensed under the GNU General Public License v3.0 only. See the [LICENSE](LICENSE) file for details.

Happy Testing!
