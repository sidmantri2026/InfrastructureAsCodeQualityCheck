# bad_example.py
# ──────────────────────────────────────────────────────────────────────────────
# BAD EXAMPLE — Infrastructure Automation Script
# This file intentionally violates Python IaC quality rules.
# Run:  python3 reviewer.py bad_example.py
# Expected: 30+ violations across Security, Linting, Error Handling,
#           Hardcoding and Idempotency categories.
# ──────────────────────────────────────────────────────────────────────────────
# PY-LINT-001: Missing module-level docstring (the block above is a comment, not a docstring)
# PY-LINT-007: No if __name__ == "__main__" guard
# PY-ERR-010:  No logging.basicConfig() configured
# PY-IDEM-005: No checkpoint/state tracking
# PY-IDEM-007: No dry_run support

import boto3
import subprocess
import requests
import yaml
import pickle
import random
import sys
from os.path import *   # PY-LINT-005: wildcard import

# ── PY-SEC-001: Hardcoded credentials ─────────────────────────────────────────
DB_PASSWORD = "MyS3cr3tPass123!"
API_TOKEN   = "ghp_abc123secrettoken456xyz"
SECRET_KEY  = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY"

# ── PY-SEC-002: Hardcoded cloud account ID ────────────────────────────────────
account_id = "123456789012"

# ── PY-HARD-001: Hardcoded IP address ────────────────────────────────────────
DB_HOST  = "10.20.30.45"
DB_HOST2 = "192.168.1.100"

# ── PY-HARD-002: Hardcoded internal hostname ──────────────────────────────────
API_URL = "https://api.internal.company.com/v1/deploy"

# ── PY-HARD-004: Hardcoded AWS region ────────────────────────────────────────
REGION = "eu-west-1"

# ── PY-HARD-005: Hardcoded port numbers ──────────────────────────────────────
DB_PORT = 5432
CACHE_PORT = 6379

# ── PY-HARD-006: Hardcoded username ──────────────────────────────────────────
username = "deploy_service_account"

# ── PY-HARD-007: Hardcoded S3 bucket name ────────────────────────────────────
bucket = "prod-backups-eu-west-1"

# ── PY-HARD-008: Module-level constants that should be config ─────────────────
NOTIFY_EMAIL  = "infra@company.com"
DEPLOY_TARGET = "prod-cluster-01"
LOG_PATH      = "/var/log/deploy/output.log"


import logging  # PY-LINT-009: import not at top of file


# ── PY-LINT-002: Function with no docstring ───────────────────────────────────
# ── PY-LINT-012: camelCase function name instead of snake_case ───────────────
def createS3Bucket(bucketName, awsRegion):
    # PY-IDEM-001: No existence check before create
    # PY-IDEM-006: No resource tagging after create
    # PY-ERR-003:  No try/except around cloud API call
    client = boto3.client("s3", region_name=awsRegion)
    client.create_bucket(Bucket=bucketName)
    print("Bucket created: %s" % bucketName)   # PY-LINT-008 + PY-LINT-004


# ── PY-LINT-002: Another function with no docstring ──────────────────────────
def deployApp(env):
    # PY-HARD-003: Hardcoded environment name in logic
    if env == "prod":
        enable_protection = True
    elif env == "staging":
        enable_protection = False

    # PY-LINT-010: Single-character variable name
    r = requests.get(
        API_URL,
        verify=False,  # PY-SEC-005: SSL verification disabled
        headers={"Authorization": "Bearer " + API_TOKEN}
    )

    # PY-SEC-007: yaml.load without Loader
    d = r.text
    config = yaml.load(d)

    # PY-SEC-004: eval() on dynamic input
    result = eval(config.get("extra_config", "{}"))

    # PY-SEC-009: String-formatted SQL (SQL injection risk)
    import sqlite3
    conn = sqlite3.connect("deployments.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO deployments (env, status) VALUES ('%s', 'started')" % env)
    conn.commit()

    # PY-SEC-010: Manual /tmp path instead of tempfile module
    tmp_path = "/tmp/deploy_" + env + ".sh"
    # PY-ERR-005: open() without context manager
    f = open(tmp_path, "w")
    f.write("#!/bin/bash\necho deploying to " + env)
    f.close()

    # PY-SEC-003: subprocess with shell=True
    # PY-ERR-004: subprocess without check=True
    subprocess.run("chmod +x " + tmp_path + " && " + tmp_path, shell=True)

    # PY-IDEM-002: Writing file without existence check
    with open("/etc/deploy/config.json", "w") as cfg_file:
        cfg_file.write("{}")

    print("Deployment done for %s" % env)  # PY-LINT-008 + PY-LINT-004


# PY-LINT-002: No docstring
def generateToken():
    # PY-SEC-006: random instead of secrets for crypto token
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    token = "".join(random.choice(chars) for _ in range(32))
    return token


# PY-LINT-002: No docstring
# PY-LINT-012: camelCase
def loadConfig(configPath):
    # PY-SEC-008: pickle.load on untrusted file
    with open(configPath, "rb") as f:
        return pickle.load(f)


# PY-LINT-002: No docstring
def deleteOldSnapshots(cutoff):
    import boto3  # PY-LINT-009: import inside function
    ec2 = boto3.client("ec2", region_name=REGION)

    snapshots = ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
    for s in snapshots:
        # PY-IDEM-003: No existence/state check before delete
        # PY-ERR-003:  No try/except around cloud API call
        ec2.delete_snapshot(SnapshotId=s["SnapshotId"])
        print("Deleted snapshot: " + s["SnapshotId"])   # PY-LINT-008


# PY-LINT-002: No docstring
def checkPermissions(user_role):
    # PY-SEC-011: assert used for security validation
    assert user_role == "admin", "Must be admin to deploy"
    assert DB_PASSWORD is not None, "Password must be set"


# PY-LINT-002: No docstring
def runDatabaseMigration(version):
    import logging
    logger = logging.getLogger(__name__)

    try:
        conn = None
        # PY-ERR-009: raise Exception() not a specific type
        if version is None:
            raise Exception("Version cannot be None")

        # PY-SEC-012: Logging credentials
        logger.info("Connecting with password=%s" % DB_PASSWORD)
        logger.debug("Using token: " + API_TOKEN)

        # ... migration logic ...
        pass

    except Exception as e:
        # PY-ERR-007: Exception logged without exc_info
        logger.error("Migration failed")
        # PY-ERR-002: Exception swallowed (no re-raise, no sys.exit)
        pass


# PY-LINT-002: No docstring
# PY-LINT-006: Using sys.argv directly instead of argparse
def main():
    # PY-LINT-006: sys.argv indexing
    env     = sys.argv[1]
    version = sys.argv[2]

    # PY-ERR-006: logger.error without sys.exit(1) after
    logger = logging.getLogger(__name__)

    try:
        checkPermissions("viewer")     # will fail but exception not caught here
        createS3Bucket(bucket, REGION)
        deployApp(env)
        runDatabaseMigration(version)
        deleteOldSnapshots("2024-01-01")

    except:   # PY-ERR-001: bare except
        # PY-ERR-002: Silent exception swallow
        logger.error("Deployment pipeline failed")
        # PY-ERR-006: No sys.exit(1) — exits 0 even on failure!


main()  # PY-LINT-007: No if __name__ == "__main__" guard
