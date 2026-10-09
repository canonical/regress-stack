# Explicit multinode deployments

Explicit deployments use an inventory and configure a complete fixed role;
they reject missing packages rather than dropping modules. Setup does not
restrict the Ubuntu release or Nova version. The existing
`regress-stack setup [TARGET]` command retains its installed-package
single-node discovery.

The `hyperconverged` profile has exactly three controllers. Each runs the same
control-plane, compute, and Ceph services. The `control` profile has one or
three controllers running control-plane and Ceph services, without
`nova-compute`; it requires at least one entry in `computes`. Three-controller
profiles use a separate API VIP. Additional entries in `computes` run Nova
compute, OVN host, and the metadata agent with Ceph client access. The
`single` profile has one controller and may also have compute-only nodes. Both
one-controller profiles use the controller address as the API endpoint and do
not provide controller HA.

Inventories may be JSON (`.json`) or YAML (`.yaml`/`.yml`), with identical
fields and validation. The checked-in [JSON example](multinode-inventory.json)
uses `hyperconverged`; set `profile` to `control` and add one or more `computes`
for separate control and compute nodes. Preseeds remain private JSON files.

An inventory may set `"disabled_modules": ["heat", "magnum", "watcher"]` to
omit those services and their packages from an explicit profile. Magnum must
also be disabled when Heat is disabled because it depends on Heat. Other
modules cannot be disabled through this field.

VM provisioning, package installation, file transfer, command execution on each
VM, and failure injection belong to the operator or CI harness. Regress-stack
runs local commands and uses service protocols to join shared state. It does
not run SSH, provision VMs, or transfer preseeds.

## VM and network contract

Use fresh disposable VMs. Converting an existing deployment is unsupported.
Give each VM a hostname matching its inventory name, a management interface with
its inventory IPv4 address, and a separate provider interface without host IP
addresses. All management addresses and the reserved API VIP share one subnet.
All provider interfaces attach to the same provider network. The harness supplies
its gateway and external connectivity. Regress-stack attaches that interface to
`br-ex`; it does not assign a duplicate gateway or configure per-VM NAT.

The example addresses in [multinode-inventory.json](multinode-inventory.json) are
documentation ranges; replace them with addresses assigned by your harness.
Reserve the provider allocation range exclusively for this deployment.

For a disposable LXD example, the following creates one controller VM with a
management NIC and an unnumbered provider NIC. Repeat the `lxc init`, device,
and start commands for every node in the inventory, giving each a distinct
management address. Use a separate LXD project or test host so these network
names do not conflict with other labs:

```sh
lxc network create rs-mgmt ipv4.address=10.220.10.1/24 ipv4.nat=true ipv6.address=none
lxc network create rs-provider ipv4.address=10.220.20.1/24 ipv4.dhcp=false ipv4.nat=true ipv6.address=none
lxc init ubuntu:24.04 node1 --vm -c limits.cpu=4 -c limits.memory=16GiB -d root,size=80GiB
lxc config device override node1 eth0 network=rs-mgmt ipv4.address=10.220.10.11
lxc config device add node1 provider nic network=rs-provider name=eth1
lxc start node1
lxc exec node1 -- ip -j address show
```

Set the inventory management CIDR to `10.220.10.0/24`, reserve a different
address such as `10.220.10.10` for the API VIP in three-controller profiles,
and use `10.220.20.0/24` as the provider CIDR with gateway `10.220.20.1`.
Use provider allocation addresses outside LXD's own use. Check the guest's
actual interface names and IP assignment with the last command before writing
`management_interface` and `provider_interface` into the inventory. The
provider interface must not acquire an IP address inside the guest.

Use an isolated test network. These recipes use authenticated service protocols
but do not configure transport TLS. They expose database, messaging, storage,
OVN, and API ports to that network. Preseeds must travel over an authenticated,
confidential channel controlled by the harness.

Each storage node creates three 10 GiB file-backed OSDs for this disposable test
fixture. Allow at least 30 GiB of free storage beyond the OS and installed
packages, and capacity for the intended Tempest workloads. Images and instance
storage use shared RBD pools. The three-node profile uses replication size 3,
minimum size 2, and host-level CRUSH placement.

## Setup

1. Prepare the public inventory before installing the role's packages. On each
   controller, with the appropriate local name:

   ```sh
   regress-stack packages --inventory inventory.json --node "$(hostname --short)"
   ```

   The operator installs that package list for the chosen Ubuntu/OpenStack
   release before setup begins. Coordination prefers Valkey when both its
   server and Sentinel packages have archive candidates, falling back to Redis.
   The bootstrap freezes that choice into controller preseeds; peers use it.

   Suppress package service startup during installation (for example, with a
   temporary `policy-rc.d` returning 101, restored afterwards). Keep Apache and
   RabbitMQ stopped on unconfigured peers until their local setup starts them.
   Package-default listeners can otherwise attract bootstrap authentication
   requests before the peer has joined or received its credentials.
   Controller setup limits each service's Apache WSGI daemon to one process.

2. On the first controller:

   ```sh
   sudo regress-stack setup --inventory inventory.json --node "$(hostname --short)" \
       --export-preseeds /root/regress-preseeds
   ```

   A pre-existing export directory must be owned by the invoking user and have
   mode 0700. Preseeds have mode 0600 from creation. The bootstrap persists its
   generated credentials in `/var/lib/regress-stack/multinode/context.json`.
   Do not delete this state to retry a failed setup: that would generate a
   different deployment identity and credentials.

3. Transfer each recipient's JSON preseed to that VM with owner root and mode
   0600. Never put preseeds in CI logs or public artifacts. The operator can
   obtain the exact peer package requirements from its preseed:

   ```sh
   sudo regress-stack packages --preseed /root/node2.json
   sudo regress-stack setup --preseed /root/node2.json
   ```

   Run node 2 and then node 3. Sequential joins let the second member become
   available before the next membership change. Compute-only nodes join after
   the controllers have formed. No configuration command returns to node 1.

4. On a controller after all local setups:

   ```sh
   sudo regress-stack ready
   sudo regress-stack test --state /var/lib/regress-stack/multinode/context.json
   ```

   Tempest uses the locally saved deployment credentials. Without `--state`,
   `test` refuses to run on a machine with explicit deployment state. Controller `auth.rc`
   files are private. A compute-only preseed contains no database recovery,
   Keystone administrator, Ceph administrator, or coordination credentials.

## Setup completion and readiness

The bootstrap starts singleton service memberships and creates shared resources
before exporting join information. It does not wait for planned peers. Ceph
pools have their final replication settings immediately; workloads should not
start while their replicas are unavailable. A successful local setup is not a
claim that the deployment is ready.

Joining controllers use native MySQL Group Replication, RabbitMQ clustering,
Ceph monitor membership, OVN Raft membership, and Redis/Valkey replication with
Sentinel. Join-side RabbitMQ queue growth adds replicas to queues created before
all members were present. Nova's periodic host discovery registers later
computes without a final bootstrap-side command.

HAProxy runs on every controller. Its local database listener selects a writable
MySQL primary. Keepalived moves the API VIP. API frontend ports are the service's
standard port plus 10000 (for example Keystone uses 15000); the catalog and
service clients use those endpoints. OpenStack daemons retain their package
backend ports. Cinder workers have distinct host identities in one shared
active-active cluster and use Redis/Valkey coordination. Keystone keys, Barbican
keys, Heat's encryption key, and Ceph identities are shared as required.

`ready` checks live membership, RabbitMQ queue replicas, usable Ceph placement
groups, OVN database connectivity, Sentinel agreement, compute/volume service
registration, authentication, and core APIs. A failed check returns a nonzero
status and a check name; it does not dump authentication errors or secrets.
Readiness does not create a workload or inject a failure.

On restarts, service-native persisted membership handles rejoining. Setup
checkpoints prevent a normal rerun from resetting completed cluster membership
or restoring Sentinel's original primary. An interrupted first bootstrap or
join may require examining local service state before retrying; this is not a
cluster disaster-recovery tool. Never enable persistent MySQL bootstrap mode.

## External HA acceptance

Validation recorded on 2026-09-09 passed native Tempest and persistent per-host
I/O on Jammy/Yoga, Noble/Caracal, and Resolute/Gazpacho. Each release was tested
with three hyperconverged nodes, three hyperconverged nodes plus one compute,
and one controller plus three computes. Controller failover results varied:

- **Noble/Caracal:** individual controller failure/return checks passed on both
  hyperconverged topologies after convergence.
- **Jammy/Yoga:** the three-node topology passed; the four-node topology had an
  unresolved new-instance creation failure during controller-2 loss, with a
  missing Nova RPC reply queue.
- **Resolute/Gazpacho:** controller failover was blocked by the archive Neutron
  issue [LP #2161232](https://bugs.launchpad.net/neutron/+bug/2161232).

The harness must record package versions and the inventory, then:

1. Establish full readiness, run Tempest, and place identifiable workloads on
   all three computes. Record the nodes hosting them.
2. Abruptly stop one VM, including the bootstrap in a separate iteration.
3. Wait for the surviving services to converge, then run
   `regress-stack ready --unavailable-node NAME` on a surviving controller.
4. Through the VIP, authenticate and create a new image/volume/instance as
   appropriate to the scenario. Verify I/O and connectivity for existing
   workloads on the surviving computes.
5. Restart the stopped VM, require full readiness again, and check membership
   recovery and subsequent workload creation. Repeat for each controller.

Recovering instances from the failed compute is outside this scenario and is
reserved for later Masakari work. Redis/Valkey Sentinel uses asynchronous
replication; the accepted backend choice does not establish preservation of an
in-flight distributed lock across failover. The acceptance evidence must state
which workload and lock conditions were exercised.

## Developer verification

PR CI runs three-node hyperconverged deployments on Jammy/Yoga, Noble/Caracal,
and Resolute/Gazpacho, using the corresponding Ubuntu archive packages. Each
matrix job uses a `2xlarge-extra` self-hosted runner and three LXD VMs with
8 vCPUs, 24 GiB RAM, and 80 GiB disks. The host requires KVM, at least 26 CPUs,
80 GiB of available RAM, and 270 GiB of free space in its LXD storage pool.

Terraform provisions the three VMs and the management/provider bridges using the
pinned LXD provider. LXD selects unused IPv4 /24s; the guests use static
management addresses and unnumbered provider interfaces. The public inventory
is a Terraform output. VM instances use no inherited profiles and live in a
private test project. Bridge networks live in the default LXD project.

Shell scripts install each node's declared packages, bootstrap the first
controller, transfer private preseeds, and join the other controllers in order.
They require readiness on all three nodes, run Tempest without retries, and
check readiness again. They do not inject controller failures or establish HA
recovery. Package installation temporarily suppresses service startup and
restores any existing `policy-rc.d` afterwards.

CI exposes provisioning, installation, setup, readiness, and Tempest as separate
steps. Each command reports its node, phase, timestamp, and exit status, with a
heartbeat during long commands. Stdout and stderr are captured separately;
only package-command stdout is passed to APT. Tempest test names, worker IDs,
durations, outcomes, and final pass/skip/fail totals are streamed while tests
run and retained in `node1-tempest.stdout.log`. Live test output includes only
recognized result and numeric summary lines. Arbitrary attachments, tracebacks,
and skip reasons are excluded because they can contain credentials.
A small Python log filter handles
structured credential inventories and encoded secrets; it does not provision
or orchestrate the deployment. Output from credential-bearing commands is
published only after filtering against the available node state and Tempest
configuration. If that credential inventory is incomplete, output is withheld
and the node, phase, and exit status remain visible.

Diagnostics and cleanup run even after a failed CI step. Terraform destroys
only resources recorded in the test's private state. Temporary forwarding rules
permit traffic from the test bridges and established replies, without flushing
existing firewall rules or changing host policies. Cleanup removes those rules.

To run the same deployment on an initialized LXD host, install Terraform 1.7+
(CI pins 1.16.4), `jq`, Python 3, and `iptables`, then run:

```sh
sudo bash tests/functional/multinode/run.sh all --release noble \
    --work-dir /tmp/regress-multinode-private \
    --artifacts /tmp/regress-multinode-results --pool default
```

The private work directory and artifact directory must not already exist and
must not contain one another. Use the `check-host` phase with the same arguments
for the read-only prerequisite check. Omit `--pool` when LXD has exactly one
storage pool. The work directory contains private raw output and runtime
credential files; never upload it. Preseeds stay outside Terraform state and
are transferred through the local LXD socket with mode 0600. Cleanup removes
the private work directory on success. Failed cleanup retains state for an
explicit retry with the `cleanup` phase and the same arguments.

Artifacts contain the public inventory, tested commit, package manifests,
per-command filtered stdout/stderr, and phase exit statuses in `result.json`.
They do not contain Terraform state, preseeds, service configuration, or Tempest
credentials. Single-node tests continue to use Spread.

Check the provisioning configuration and scripts without creating VMs:

```sh
terraform -chdir=tests/functional/multinode init -backend=false -lockfile=readonly
terraform -chdir=tests/functional/multinode fmt -check
terraform -chdir=tests/functional/multinode validate
terraform -chdir=tests/functional/multinode test
shellcheck tests/functional/multinode/*.sh
```

Terraform tests use a mock provider. Python unit tests execute the real shell
entry point with fake host commands. These checks do not establish a live
three-VM OpenStack deployment.

Run the native checks:

```sh
uv run py.test
uv run mypy
uv run ruff check .
```

An additional opt-in test launches three local server/Sentinel pairs on loopback
addresses and exercises Tooz contention, bootstrap process loss, election, and
rejoin. It does not install services or configure the host network:

```sh
REGRESS_COORDINATION_SERVER=/path/to/archive/valkey-server \
    uv run --with 'tooz[redis]==6.0.1' py.test \
    tests/integration/test_coordination_live.py
```

The same test accepts a Redis server binary. Tooz 6.0 forwards the data-server
password to Sentinel, so these recipes use matching credentials when Sentinel
authentication is supported. Tooz versions before 6.0, including Jammy's 2.10,
use Sentinel without client authentication; Redis clients, replication, and
Sentinel connections to Redis still authenticate. Restrict Sentinel access to
trusted deployment peers on the isolated test network. Bootstrap freezes this
choice in the controller preseeds using the installed Tooz version.

Unit tests and this protocol test do not establish full three-VM OpenStack HA;
that requires the external acceptance sequence above.
