#!/bin/python3

import sys
import os

sys.path.insert(1, f"{os.environ['SSO']}/src/")

from sso import common
from sso.cs import CassandraStress
from sso.common import Iteration
from sso.scylla import Scylla
from sso.hdr import parse_profile_summary_file
from sso.cassandra import Cassandra
from datetime import datetime

print("Test started at:", datetime.now().strftime("%H:%M:%S"))

if len(sys.argv) < 2:
    raise Exception("Usage: ./benchmark_new_node.py [PROFILE_NAME]")

profile_name = sys.argv[1]

# Load properties
props = common.load_yaml(f'{profile_name}.yml')
env = common.load_yaml(f'environment_{profile_name}.yml')

start_count = props['start_count']
new_count = len(env['cluster_private_ips']) - start_count

cluster_private_ips = env['cluster_private_ips'][:start_count]
cluster_string = ",".join(cluster_private_ips[:start_count])
new_node_private_ips = env['cluster_private_ips'][start_count:]

cluster_public_ips = env['cluster_public_ips'][:start_count]
new_node_public_ips = env['cluster_public_ips'][start_count:]

all_public_ips = env['cluster_public_ips']
all_private_ips = env['cluster_private_ips']

loadgenerator_public_ips = env['loadgenerator_public_ips']
loadgenerator_count = len(loadgenerator_public_ips)

# Run parameters

# Row size of default cassandra-stress workload.
# Measured experimentally.
ROW_SIZE_BYTES = 210 * 1024 * 1024 * 1024 / 720_000_000

# 200GB per node
TARGET_DATASET_SIZE = len(cluster_private_ips) * 200 * 1024 * 1024 * 1024

REPLICATION_FACTOR = 3
ROW_COUNT = int(TARGET_DATASET_SIZE / ROW_SIZE_BYTES / REPLICATION_FACTOR)

BACKGROUND_LOAD_OPS = 25000

# Start Scylla/Cassandra nodes (except ones to be started later)
if props['cluster_type'] == 'scylla':
    s = Scylla(all_public_ips, all_private_ips, all_private_ips[0], props)
    s.install()
    s = Scylla(cluster_public_ips, cluster_private_ips, cluster_private_ips[0], props)
    s.start()
else:
    cassandra = Cassandra(all_public_ips, all_private_ips, all_private_ips[0], props)
    cassandra.install()
    cassandra = Cassandra(cluster_public_ips, cluster_private_ips, cluster_private_ips[0], props)
    cassandra.start()

print("Nodes started at:", datetime.now().strftime("%H:%M:%S"))

# Setup cassandra stress
cs = CassandraStress(env['loadgenerator_public_ips'], props)
cs.install()
cs.prepare()

print("Loading started at:", datetime.now().strftime("%H:%M:%S"))

cs.stress_seq_range(ROW_COUNT, 'write cl=QUORUM', f'-schema "replication(strategy=SimpleStrategy,replication_factor={REPLICATION_FACTOR})" -log hdrfile=profile.hdr -graph file=report.html title=benchmark revision=benchmark-0 -mode native cql3 -rate "threads=30" -node {cluster_string}')

print("Run started at:", datetime.now().strftime("%H:%M:%S"))

# Background load
background_load = cs.loop_stress(f'mixed ratio\\(write=1,read=1\\) duration=5m cl=QUORUM -pop dist=UNIFORM\\(1..{ROW_COUNT}\\) -log hdrfile=profile.hdr -graph file=report.html title=benchmark revision=benchmark-0 -mode native cql3 -rate "threads=100 fixed={BACKGROUND_LOAD_OPS // loadgenerator_count}/s" -node {cluster_string}')

add_nodes_start = datetime.now()

iteration = Iteration(f'{profile_name}/add-node', ignore_git=True)

# Start Scylla/Cassandra nodes
if props['cluster_type'] == 'scylla':
    s = Scylla(new_node_public_ips, new_node_private_ips, all_private_ips[0], props)
    s.start()
else:
    cassandra = Cassandra(new_node_public_ips, new_node_private_ips, all_private_ips[0], props)
    cassandra.start()

add_nodes_end = datetime.now()

print("Run ended at:", datetime.now().strftime("%H:%M:%S"))
print("Adding nodes took:", (add_nodes_end - add_nodes_start).total_seconds(), "seconds.")

with open(f'{iteration.dir}/result.txt', 'a') as writer:
    writer.write(f'Adding nodes took (s): {(add_nodes_end - add_nodes_start).total_seconds()}\n')

background_load.request_stop()
background_load.join()
print("Background load ended:", datetime.now().strftime("%H:%M:%S"))
