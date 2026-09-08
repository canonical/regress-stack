# Multinode validation record

Noble/Caracal results recorded 2026-09-09. All three requested topologies
passed complete native Tempest runs and per-host persistent workload checks. Individual controller
failure/return acceptance passed on both hyperconverged topologies; the
four-node topology also passed compute-only failure/return acceptance.
Results below distinguish these boundaries from unattended fresh setup,
uninterrupted availability, and automatic instance evacuation.

| Boundary | Executed check | Result |
| --- | --- | --- |
| Native unit suite | `uv run py.test -q tests` | 142 passed, one opt-in process integration test skipped |
| Lint | `uv run ruff check .` | Passed |
| CLI parsing | Actual setup, packages, and ready help paths and inventory package selection | Passed on the development host; package candidates there are Resolute |
| Redis protocol | Noble Redis 7.0.15-1ubuntu0.24.04.4, Sentinel, Tooz 6.0.1 process integration | Passed |
| Valkey protocol | Noble Valkey 7.2.13+dfsg1-0ubuntu0.1, Sentinel, Tooz 6.0.1 process integration | Passed |
| Three hyperconverged | Native setup and readiness; complete resumed native Tempest | 165 tests: 154 passed, 11 skipped, zero failed, 2610.4983 seconds |
| Controller also computing plus three computes | Setup, resumed setup, readiness, and complete native Tempest | 165 tests: 154 passed, 11 skipped, zero failed, 2276.5986 seconds |
| Three hyperconverged plus one compute | Setup, readiness, native Tempest, and individual node failure acceptance | Setup required interventions below; Tempest: 154 passed, 11 skipped, zero failed; all four failure/return cases passed after convergence |

## Verification boundaries

Redis and Valkey process tests exercised contention, bootstrap process loss,
Sentinel election, new lock acquisition, and replica rejoin. They ran
extracted archive binaries on the Resolute development host with `uv`
Python dependencies and an extracted Resolute `liblzf1`. They do not prove
Noble systemd or confinement behavior. Tooz 6.0.1 requires matching
server/Sentinel authentication credentials in this configuration.

The process test releases its initial lock before failure injection. It
checks availability after election, not preservation of an in-flight lock.
See [multinode.md](multinode.md) for the external acceptance boundary and
coordination limitations.

The successful three-node Tempest result came from the native
`regress-stack test --concurrency 1` path. `stestr last` reported the totals
above and `stestr failing --list` returned no failures. This completed run
preceded the later HAProxy failover and relay-log durability changes.

## Three-node failure acceptance

Each controller has an Ubuntu instance, a floating IP, and an attached
1 GiB volume. Checks read persistent root/data markers and append and sync
volume data over SSH. A separate new instance and attached volume exercise
API and scheduling availability after membership convergence.

| Abruptly stopped controller | Degraded readiness | Surviving I/O and new workload | Automatic MySQL rejoin and full readiness |
| --- | --- | --- | --- |
| Node 1, secondary in final repeat | Passed after RabbitMQ convergence | Passed after convergence; an earlier build during convergence failed | Passed |
| Node 2, current primary | Passed after RabbitMQ convergence | Passed | Passed |
| Node 3 | Passed after convergence | Passed | Passed |

Instances on the stopped compute are restarted by the operator after host
return, then their original root/data markers are checked again. This is
fixture maintenance, not automatic instance evacuation or Masakari evidence.
All three fixture checks passed after each controller returned.

The first node-1 failure exposed retained HAProxy MySQL connections to the
failed primary. MySQL server entries now use
`on-marked-down shutdown-sessions`; generated configuration passed the
installed HAProxy validator. Later primary-loss acceptance required no
manual HAProxy or application restart.

Abrupt restarts also exposed corrupt MySQL applier relay logs. The generated
Group Replication configuration now sets `sync_relay_log=1`, with a regression
test. Before clearing the already damaged node-1 channel, its executed and
received transaction sets were checked with `GTID_SUBSET` against both ONLINE
survivors; both covered all transactions. The stopped applier channel was
reset once and the member joined the existing group without bootstrap.
Subsequent abrupt-stop tests of all three nodes rejoined automatically without
SQL resets. These observations do not prove recovery from every crash timing.

During node-1 convergence, an early create request timed out and its HTTP
retry created a duplicate test server. The build encountered a Nova RPC reply
timeout. Both failed attempts were deleted. A fresh request after degraded
readiness passed completed SSH and attached-volume I/O checks. Readiness
samples taken before RabbitMQ membership converged failed as expected; they
must not be presented as uninterrupted API availability.

Provider NICs initially remained DOWN after reboot because setup only used
transient link activation. An enabled systemd unit now activates the provider
bridge and NIC after Open vSwitch. Boot persistence was observed on all three
controllers during both graceful restarts and abrupt stops.
The OVN chassis readiness query now uses every planned Southbound endpoint;
it previously failed when the local database was no longer leader.

## Host resource incident and retained resources

Running seven 8 GiB VMs concurrently exhausted the laptop and the user
reported a crash. This was an execution error. All eight test VMs were
stopped with disks retained; the host then had 79 GiB available and no swap
use. Both original Tempest attempts were incomplete and are excluded from the
completed results recorded here.

Resumed validation runs one topology at a time. A host-side guard was configured to stop test
VMs if available RAM fell below 24 GiB or swap use exceeded 512 MiB. It
remained active for the resumed runs and did not trip. No second topology
may run concurrently. A temporary user-approved 12 GiB ZFS ARC cap was
used for the four-node runs; its original setting is saved for restoration.

The total outage required operator MySQL recovery. Nodes 2 and 3 had the same
latest executed GTID range, covering received transactions, while node 1
lagged. Damaged applier relay logs were cleared on nodes 2 and 3; node 2 was
bootstrapped once with bootstrap mode reset to OFF in a `finally` block, and
peers joined it. This is not automatic full-cluster recovery evidence.
The [MySQL RESET REPLICA reference](https://dev.mysql.com/doc/refman/8.0/en/reset-replica.html)
describes channel-scoped operation and retention of GTID execution history.

All guests use Noble, four vCPUs, and a 60 GiB root disk. The archived
three-node deployment has `validated-ha3` snapshots. Its instances remained
stopped while the four-node deployment ran. Current four-node controllers
have 9 GiB RAM each and the compute-only VM has 8 GiB. The single-controller
layout started only after four-node HA acceptance finished and HA guests
stopped. The two
inventories use separate provider allocation pools on `rsm-provider` and
management addresses on `rsm-mgmt`.

Live archive packages include Nova `3:29.2.0-0ubuntu1.8`, Cinder
`2:24.2.0-0ubuntu2.1`, Tooz `6.0.1-0ubuntu1`, RabbitMQ
`3.12.1-1ubuntu1.2`, Valkey `7.2.13+dfsg1-0ubuntu0.1`, Ceph
`19.2.3-0ubuntu0.24.04.3`, and MySQL 8.0.46.

## Three hyperconverged plus one compute

The user approved a temporary 12 GiB host ZFS ARC cap and applied it using
interactive sudo. The effective cap was observed at 12884901888 bytes; host
available memory rose to 66 GiB. The original raw value was zero (automatic),
with effective maximum 96814624768 bytes, saved for restoration. The guard
remained active throughout validation and was stopped after all test guests
were verified stopped.

The three-node deployment is retained stopped as `rsm-ha3-node1` through
`rsm-ha3-node3`, with `validated-ha3` snapshots. Autostart is disabled and
the archived instances' management address reservations are released; their
snapshots retain the original configuration. Prepared snapshots were copied
to `rsm-node1` through `rsm-node3`, with four vCPUs and 8 GiB RAM each.
The archived deployment remained stopped throughout this four-node HA run.

The fresh bootstrap completed, but unattended guest upgrades then restarted
its singleton MySQL group before peers joined. No member was ONLINE and
both peers had empty executed GTID sets. The sole data-bearing bootstrap
was started once in bootstrap mode, then bootstrap mode was reset to OFF.
Guest update timers were stopped for the validation window after active
updates completed. Repeated upgrade-driven Ceph restarts also hit the
bootstrap's systemd start limits; those units were reset and started.
Bootstrap Nova conductor and scheduler exited on AMQP authentication errors
while unconfigured peer brokers were running, and were started after joins.
This run required operator intervention during setup; it does not establish
an uninterrupted fresh deployment.

The third copied controller retained a dynamic DHCP lease at `.123` despite
its `.13` reservation. Preflight correctly rejected it before mutation.
The operator assigned the inventory address persistently through Netplan
and retried the same preseed. All controller and compute setups then
completed. Full native readiness passed with all four compute registrations,
nine OSDs up, and 65 active/clean placement groups. The other four-node
topology remained stopped throughout this HA run.

The four-node native Tempest run completed: 165 tests in 2750.7918 seconds,
154 passed, 11 skipped, zero failures. The runner exited zero and
`stestr failing --list` was empty. Its multinode scheduling scenario passed.
The result is saved as `ha4-tempest-results.log` in the private operator
state directory.

Four persistent Ubuntu instances with attached 1 GiB volumes passed SSH
and marker/I/O checks. Their allocation pushed the original 5 GiB OSDs
toward capacity and triggered placement-group rebalancing. Each OSD backing
file was expanded to 10 GiB within the existing 60 GiB VM root disk. The
operator stopped one OSD at a time, checked `ceph osd ok-to-stop` before
each subsequent stop, expanded BlueFS with `ceph-bluestore-tool`, restarted
the OSD, and verified its new size and UP state. All nine OSDs now provide
90 GiB raw capacity. Full readiness and all four fixture I/O checks passed
after expansion. No Ceph memory target was changed.

Ceph also reported intermittent monitor clock skew. The existing NTP client
on each test VM was configured with a 64-second maximum poll and a common
Ubuntu NTP server, 185.125.190.58. After synchronization, Ceph was observed
HEALTH_OK before failure injection. Controllers were raised from 8 to 9 GiB
RAM while stopped during failure acceptance; this remains within the
requested range and avoids the low internal memory headroom seen with
long-lived fixtures. The compute-only VM remains at 8 GiB.

All three controller failure cases passed degraded readiness, surviving
I/O, creation/read-back/deletion of a new instance and volume on node 4,
automatic MySQL rejoin, full readiness, and restoration/read-back of the
failed host's fixture. Initial readiness samples during membership and Nova
registration convergence failed before later samples passed. No database or
application repair was needed in these controller failure cases.

The first compute-only failure case overlapped unattended upgrades on
controllers 2 and 3 after controller reboots had reactivated their update
timers. Ceph units hit start limits and needed operator recovery after the
upgrades finished. All four guests' update timers were then disabled
persistently for the remaining test window. The compute failure was repeated
after upgrades finished and full readiness and fixture I/O passed.

During the repeat, a newly created instance reused a floating IP with a
saved SSH host key from a deleted test instance. SSH correctly rejected the
changed key. The operator verified the new instance's key through its Nova
console and replaced only that address's saved key, retaining strict host
key checking. Surviving I/O and new instance/volume creation, read-back, and
deletion then passed. After compute return, full readiness passed without
service repair. Operator restart/remount of its original fixture completed,
and all four fixtures passed persistent marker read-back and volume I/O.
All four individual node failure/return cases therefore passed after
convergence; this is not uninterrupted availability or automatic evacuation
evidence. Guest update timers were re-enabled before stopping the topology.

## Controller also computing plus three computes

After all HA guests were verified stopped, the retained `rsm-single1`
through `rsm-single4` guests were started sequentially. The controller has
9 GiB RAM and each compute-only guest has 8 GiB. All have four vCPUs. Guest
update timers were disabled for the validation window; neither update
service was active on any guest. Final source was transferred to every VM,
and the actual setup CLI completed on all four using the saved deployment
identity and matching inventory/preseeds. This is a resumed deployment,
not a second fresh installation.

One BUILD server left by the interrupted Tempest run was deleted. No
volumes remained. Before new workloads, each of the controller's three
OSD backing files was expanded from 5 to 10 GiB within its existing root
disk, one stopped OSD at a time. Each returned UP and reported 10 GiB.
This maintenance occurred with no instances running; the single profile
has no storage replica redundancy and does not provide HA during OSD
stops. Full native readiness passed after all four computes registered.
The fresh `regress-stack test --concurrency 1` run completed successfully:
165 tests in 2276.5986 seconds, 154 passed, 11 skipped, zero failed.
The runner exited zero, `stestr last` supplied those totals, and
`stestr failing --list` was empty. Its multinode scheduling scenario passed.
The result is retained as `single-tempest-results.log` in private operator
state. Full native readiness passed after Tempest and again after workload
acceptance. One Ubuntu instance on each of the four compute hosts passed
SSH, fresh 1 GiB volume attachment, persistent root/data marker creation,
read-back, and append/sync I/O. The harness exited zero after checking all
four fixtures together. Guest update timers were verified enabled before
stopping the topology.

## Retention and remaining boundaries

All three topologies are retained separately. `rsm-ha3-node1` through
`rsm-ha3-node3` preserve the three-node deployment with `validated-ha3`
snapshots. `rsm-node1` through `rsm-node4` preserve the four-node HA deployment
with `validated-ha4` snapshots. `rsm-single1` through `rsm-single4` preserve
the single-controller deployment with `validated-single4` snapshots. All
eleven test VMs were verified STOPPED after validation; unrelated guests
were left untouched. The host had 60 GiB available and 1.2 MiB swap used.
Guest update timers on both four-node deployments were re-enabled.
Archived HA3 management reservations were released to permit the prepared
copies to use the original addresses;
restoring that deployment requires restoring its original names/network
configuration from the saved snapshots while the HA4 deployment is stopped.
These are retained recovery points, not proof of automatic full-cluster
restart. Never start both HA deployments together: their addresses and VIP
are identical. Run only one topology at a time on this laptop.

The four-node workloads used 10 GiB OSD backing files after operator capacity
expansion; source defaults at the time were 5 GiB. Setup-time package upgrades and a
cloned DHCP lease required the interventions recorded above. These runs
establish the tested steady-state and converged single-node failure
boundaries, not an unattended fresh-install or full-outage recovery claim.
The single-controller profile has no controller or storage HA. Failed-host
instance restarts were operator actions; automatic evacuation was not tested.
Redis/Valkey lock preservation across failover remains outside the proven
boundary described above.

The temporary host ZFS ARC cap remains 12884901888 bytes after guest
shutdown. Restoring the saved original setting with `sudo -n` was rejected
because interactive authentication is required. The original raw setting
was zero (automatic), with effective maximum 96814624768 bytes. Host
restoration remains an operator step and is not claimed complete.

## Release gate removal

After live validation, the Ubuntu Noble and Nova 29.x preflight gates were
removed. Regression cases cover other Ubuntu codenames and Nova versions,
while retaining the local package check. The repository's `tests/` suite
passed 142 tests with one opt-in skip; lint passed. Unscoped pytest also
collected eight tests in the ignored local `braindump/bgp-experiment`
directory, which errored because their `context` fixture was unavailable.
Those local experiments were not changed. No additional live deployment
was run for this gate removal. Subsequent release validation is recorded below.


## Jammy/Yoga and Resolute/Gazpacho cloud validation

The follow-up matrix was executed on an external OpenStack cloud through the
operator bastion, using at most two deployments and eight test VMs concurrently,
with four vCPUs, 8 GiB RAM and a 50 GiB root disk per VM. Controllers have
4 GiB swap. Completed intermediate deployments are deleted only after their
private evidence archives are copied locally and their hashes checked. The
final four-node HA deployment for each release is retained.

| Release | Topology | Native Tempest | Persistent per-host I/O | Failure/return |
| --- | --- | --- | --- | --- |
| Jammy/Yoga | Controller also computing plus three computes | 153 passed, 11 skipped, zero failed; 1519.1174 s | Passed on all four hosts | Not an HA topology |
| Jammy/Yoga | Three hyperconverged | 153 passed, 11 skipped, zero failed; 1468.7307 s | Passed on all three hosts | All three controller loss/return cases passed after the corrections below |
| Jammy/Yoga | Three hyperconverged plus one compute | 153 passed, 11 skipped, zero failed; 2143.3736 s | Passed on all four hosts | Nodes 1, 3, 4 passed; node 2 failed new-instance creation |
| Resolute/Gazpacho | Controller also computing plus three computes | 162 passed, 15 skipped, zero failed; 1938.3797 s | Passed on all four hosts | Not an HA topology |
| Resolute/Gazpacho | Three hyperconverged | 162 passed, 15 skipped, zero failed; 2275.0877 s | Passed on all three hosts before failure | Blocked: controller-1 loss failed surviving I/O; stock Neutron LP #2161232 |
| Resolute/Gazpacho | Three hyperconverged plus one compute | 162 passed, 15 skipped, zero failed; 1858.5044 s | Passed on all four hosts | Controllers blocked by archive Neutron issue; compute-only loss/return passed |

Jammy uses the archive Tooz package without a backport. Its Redis service
requires authentication; Sentinel client authentication is omitted because
this Tooz version cannot send Sentinel credentials. Readiness uses Cinder's
service REST endpoint to accommodate the older OpenStack SDK.

The first Resolute runs exposed two resource limits. Apache mod_wsgi daemon
process counts exceeded the intended single-worker service configuration;
MySQL was OOM-killed during Heat tests. Controller setup now limits matching
WSGI daemon processes to one. Later, four-host Tempest instance creation
filled the original three 5 GiB OSDs. Existing validation OSDs were expanded
one at a time to 10 GiB; new multinode OSDs now start at that size. Neither
failed run counts as acceptance. Their logs and test records are retained.

Resolute's block-storage catalog entry also required explicit Cinder Tempest
capability discovery: the packaged tempestconf only detected volumev3.
The corrected native configuration queries advertised microversions and
backend services, enables Cinder testing, and preserves legacy discovery.

Jammy's first HA bootstrap encountered package-default listeners on peers
before their configuration. Those peer listeners were stopped and native
setup resumed. The external package preparation now suppresses package
service startup until native setup configures each node. This resumed run
is not evidence of an uninterrupted fresh installation.

Jammy HA failure testing exposed Redis 6.0 Sentinel's use of its first bind
address for outbound connections. Listing loopback first prevented peer
and replica discovery, although configured primary-address queries passed.
The generator now lists the management address first, and readiness also
requires each Sentinel's election quorum check to succeed. After applying
that correction, the live cluster reported three usable Sentinels and two
replicas, and elected a surviving primary when controller 1 was stopped.
All three controller failure/return cases subsequently passed.

Failure injection issues an abrupt guest power-off and verifies outer-cloud
SHUTOFF state. When this cloud restarts a guest immediately, the harness uses
the cloud stop operation to hold it down. Successful cases demonstrate availability
after node-loss convergence; they do not all represent an uninterrupted
hypervisor hard power cut. The first attempt also exposed an SSH session
that remained open after power-off; a transport timeout now requires the
same cloud-state confirmation rather than being treated as success alone.

Jammy's third-controller loss also exposed RabbitMQ 3.9's default 60-second
queue-listing deadline. The metadata query returned all 135 queues and all
87 durable queues had three members. Adding the per-replica `online` probe
exceeded that deadline. The same query with a 120-second deadline returned
all rows and passed the existing replica checks. Native readiness now uses
that deadline without changing its required membership or live majority.

The Jammy and Resolute single-controller deployments and Jammy HA3 were
deleted after their node archives were copied locally, SHA-256 hashes
matched, and successful native results and fixture I/O checked inside the
archives. Jammy HA3 also has all three failure/return acceptance records.
Resolute HA3 was also deleted after its passing suite and initial I/O, failed
controller-loss case, and archive-package blocker were preserved. Its record
is marked blocked, not passed.

Suppressing package service startup exposed Nova's implicit dependency on
libvirt already running; multinode preparation now explicitly starts it
before the native module defines its Ceph secret. Resolute HA3 also exposed
inactive Memcached. The native Keystone module already enables caching to
mitigate the Python 3.14 connection accumulation described in LP #2154897,
but that mitigation requires a running cache. Preparation now starts
Memcached explicitly. Existing Resolute controllers had their cache started
and API processes restarted sequentially before a fresh full Tempest run.
The failed attempt is retained separately and excluded from acceptance.

The initial connection failure reached MySQL's default 151-connection limit.
Increasing that limit alone did not prevent Keystone's own pool exhaustion.
After cache activation, live cache hits were observed and Keystone used nine
connections in one sample, while total connections reached 137. The
three-controller configuration retains a 500-connection limit for aggregate
service-pool headroom; it is not a substitute for working Keystone caching.
Local verification after these changes passed 166 tests, with one opt-in
integration test skipped, and lint passed.

The current archive versions are:

| Package | Jammy/Yoga | Resolute/Gazpacho |
| --- | --- | --- |
| nova-common | 3:25.2.1-0ubuntu2.11 | 3:33.0.0-0ubuntu3.1 |
| cinder-common | 2:20.3.1-0ubuntu1.6 | 2:28.0.0-0ubuntu1 |
| python3-tooz | 2.10.0-0ubuntu1 | 8.1.0-2ubuntu1 |
| Coordination server and Sentinel | Redis 5:6.0.16-1ubuntu1.1 | Valkey 9.0.4-0ubuntu0.1 |
| ceph-common | 17.2.9-0ubuntu0.22.04.3 | 20.2.0-0ubuntu2 |
| mysql-server | 8.0.46-0ubuntu0.22.04.4 | 8.4.11-0ubuntu0.26.04.1 |

### Archive-package HA limits observed

Resolute HA3 passed its full native suite and initial per-host persistent I/O,
but surviving floating-IP connectivity failed with controller 1 stopped,
after native degraded readiness passed. The installed `python3-neutron`
`2:28.0.0-0ubuntu1.1` contains the handler affected by
[LP #2161232](https://bugs.launchpad.net/neutron/+bug/2161232). Network HA groups
carry both router and network IDs; the handler incorrectly pins a distributed
router through `Logical_Router.options:chassis`. The live Northbound router
was pinned and its Southbound ports were `l3gateway`, without HA chassis groups.
The [merged upstream fix](https://github.com/openstack/neutron/commit/4b863dfd7e7e63467b58e64f9b1f2f2332438e04)
ignores network HA groups in this handler. The operator chose to retain stock
archive packages and record affected HA checks as blocked. No package was
patched, and no HA pass is claimed for this deployment. Controllers 2 and 3
were not individually stopped after this blocker was established.

Jammy HA4 controller 1 passed its complete loss/return case. Controller 2
passed degraded readiness and surviving I/O, but a new server remained in
BUILD for the 300-second deadline. Its request reached Nova scheduling; the
conductor logged that the RPC reply queue did not exist and dropped the reply.
After controller return, full readiness and original fixture I/O passed.
This failed attempt is retained. The missing reply was observed directly;
a complete root-cause diagnosis and correction remain outstanding.

Jammy HA4 controllers 1 and 3 and compute-only node 4 passed all loss/return
checks, including surviving I/O, new instance/volume operations while down,
full readiness after rejoin, original fixture restoration, and a new
workload on the returned host. Controller 2 remains failed as described above.
All four original fixtures passed again after the final case. This deployment
is retained; the overall HA4 result is not a pass.

Fresh Resolute HA4 bootstrap and all joins completed using the corrected
service startup and connection-capacity configuration. Readiness passed and
Memcached was active before its native full-suite run. The known Neutron
archive blocker is retained without a package patch.

The final Resolute HA4 runner survived an SSH interruption. Its complete
native record reports 177 tests: 162 passed, 15 skipped, zero failed, in
1858.5044 seconds. The runner exited zero and the failure list was empty.
Persistent root and attached-volume I/O passed on all four hosts. The
compute-only failure/return case passed, including original fixture recovery
and a new workload on the returned host. Controller-loss checks remain
blocked by the archive Neutron issue, in accordance with the operator's decision.

Jammy HA4's DHCP-supplied NTP servers timed out. The operator configured the
existing time client to use `ntp.ubuntu.com` with a 64-second maximum poll
interval. Ceph's measured clock skew cleared before failure injection.
Controller root disks still reported low free-space percentage warnings;
no Ceph health threshold was relaxed. Placement groups were clean before
the initial host-loss cases. Update timers disabled during package preparation
are restored after each final deployment's validation.

### Cloud validation outcome and retained deployments

All six complete native Tempest runs passed, and every deployment passed
initial persistent root/attached-volume I/O on each host. HA acceptance is
not uniformly successful: Jammy HA4 controller 2 failed new-instance
creation, and Resolute controller-loss cases are blocked by the stock
Neutron bug. Passing API suites and readiness checks do not establish those
workload/failover boundaries. Resolute HA4's compute-only loss/return case
passed, and all four original fixtures passed I/O after that case.

The final deployments are `rsv-260909-jy-ha4-n1` through `-n4` and
`rsv-260909-rg-ha4-n1` through `-n4`. They are retained on the external cloud;
no local VMs were started for this matrix. Each VM has four vCPUs, 8 GiB RAM,
and a 50 GiB root disk. The four intermediate deployments were deleted only
after their evidence was preserved. Failed and blocked outcomes have
separate result records; they are never marked as acceptance passes.

Private node archives, native test records, failure logs, and result records
are stored under `/home/ubuntu/regress-validation/evidence` on the bastion
and `~/.local/state/regress-ps7-validation/evidence` on the development host.
Archives can contain service logs and HTTP authentication data and must not
be published. This document contains the shareable result summary.

At final retention, all eight outer VMs were observed ACTIVE with the
requested four-vCPU/8-GiB configuration. Native readiness passed on both
deployments. All four original fixtures on each deployment passed I/O after
its last executed failure case; Jammy passed an additional final I/O check.
Normal guest update timers were verified enabled and active on all eight VMs.
Every node archive was copied locally and its SHA-256 checked against the
bastion copy; the final native totals, empty failure lists, readiness, fixture
results, and executed failure-case outcomes were checked within the evidence.
