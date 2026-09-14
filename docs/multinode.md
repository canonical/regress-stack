# Explicit multinode deployments

Explicit deployments use an inventory and configure a complete fixed role;
they reject missing packages rather than dropping modules. Setup does not
restrict the Ubuntu release or Nova version. The existing
`regress-stack setup [TARGET]` command retains its installed-package
single-node discovery.

The `hyperconverged` profile has exactly three controllers. Each runs the same
control-plane, compute, and Ceph services. Additional entries in `computes` run
Nova compute, OVN host, and the metadata agent with Ceph client access. The
`single` profile has one controller and may also have compute-only nodes; its
API address is the controller address. It does not provide controller HA.

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
   regress-stack packages --inventory inventory.json --node node1
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
   sudo regress-stack setup --inventory inventory.json --node node1 \
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
   sudo regress-stack test
   ```

   Tempest uses the locally saved deployment credentials. Controller `auth.rc`
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
