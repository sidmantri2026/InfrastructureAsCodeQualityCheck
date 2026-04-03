"""
Infrastructure Deployment Script — AWS S3 + Application Deploy + Database Migration.

Usage:
    python3 good_example.py --env prod --version 1.4.2 [--dry-run]

Required environment variables:
    DB_PASSWORD          Database password (from Vault or Secrets Manager)
    API_TOKEN            Deployment API bearer token
    AWS_ACCOUNT_ID       AWS account ID
    DB_HOST              Database hostname
    API_URL              Deployment API base URL
    BACKUP_BUCKET        S3 bucket name for backups
    DB_USER              Database service account username
    AWS_DEFAULT_REGION   AWS region (default: eu-west-1)
    DB_PORT              Database port (default: 5432)

Exit codes:
    0  — All steps completed successfully
    1  — One or more steps failed
"""

# ── Standard library imports (all at top — PY-LINT-009) ──────────────────────
import argparse
import json
import logging
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Third-party imports ───────────────────────────────────────────────────────
import boto3
import requests
import yaml
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential

# ── Logging configured before first use (PY-ERR-010) ─────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration from environment — no hardcoded values (PY-HARD-*) ─────────
DB_PASSWORD   = os.environ.get("DB_PASSWORD")
API_TOKEN     = os.environ.get("API_TOKEN")
ACCOUNT_ID    = os.environ.get("AWS_ACCOUNT_ID")
DB_HOST       = os.environ.get("DB_HOST")
API_URL       = os.environ.get("API_URL")
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET")
DB_USER       = os.environ.get("DB_USER")
REGION        = os.environ.get("AWS_DEFAULT_REGION", "eu-west-1")
DB_PORT       = int(os.environ.get("DB_PORT", "5432"))

# ── Valid environment names from config — not hardcoded in logic (PY-HARD-003) ─
PROTECTED_ENVS = os.environ.get("PROTECTED_ENVS", "prod,production").split(",")


# ── Helper: load state file for checkpoint tracking (PY-IDEM-005) ─────────────
def load_state(state_file: Path) -> dict:
    """
    Load deployment state from a JSON checkpoint file.

    Args:
        state_file: Path to the state JSON file.

    Returns:
        dict: Current state, or empty dict if file does not exist.
    """
    if state_file.exists():
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load state file: {e}")
    return {}


def save_state(state_file: Path, state: dict) -> None:
    """
    Persist deployment state to a JSON checkpoint file.

    Args:
        state_file: Path to write the state JSON.
        state:      State dictionary to persist.
    """
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
    logger.debug(f"State saved to {state_file}")


# ── S3 bucket provisioning (PY-IDEM-001, PY-IDEM-006, PY-ERR-003) ────────────
def bucket_exists(s3_client, bucket_name: str) -> bool:
    """
    Check whether an S3 bucket already exists and is accessible.

    Args:
        s3_client:   boto3 S3 client.
        bucket_name: Name of the bucket to check.

    Returns:
        bool: True if the bucket exists, False otherwise.
    """
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            return False
        raise


def create_s3_bucket(bucket_name: str, region: str, env: str, dry_run: bool = False) -> None:
    """
    Create an S3 bucket with versioning and standard tags.
    Idempotent — skips creation if bucket already exists.

    Args:
        bucket_name: Globally unique bucket name.
        region:      AWS region code.
        env:         Environment name (used for tagging).
        dry_run:     If True, log intent without making API calls.
    """
    s3 = boto3.client("s3", region_name=region)

    # PY-IDEM-001: existence check before create
    if bucket_exists(s3, bucket_name):
        logger.info(f"Bucket already exists: {bucket_name} — skipping")
        return

    if dry_run:
        logger.info(f"[DRY RUN] Would create bucket: {bucket_name} in {region}")
        return

    try:
        # Buckets in us-east-1 do not accept a LocationConstraint
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )

        # Enable versioning
        s3.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"Status": "Enabled"},
        )

        # PY-IDEM-006: tag all created resources
        s3.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={
                "TagSet": [
                    {"Key": "Environment", "Value": env},
                    {"Key": "ManagedBy",   "Value": "python-infra-scripts"},
                    {"Key": "CreatedBy",   "Value": os.environ.get("BUILD_USER", "unknown")},
                ]
            },
        )
        logger.info(f"Created bucket: {bucket_name}")

    except ClientError as e:
        logger.error(f"Failed to create bucket {bucket_name}: {e}", exc_info=True)
        raise


# ── Application deployment (PY-SEC-005, PY-ERR-003, PY-SEC-007) ──────────────
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)  # PY-ERR-008: retry with exponential backoff for transient failures
def call_deploy_api(api_url: str, token: str, payload: dict) -> dict:
    """
    Call the deployment API with retry logic for transient failures.

    Args:
        api_url:  Full URL of the deploy endpoint.
        token:    Bearer token for authentication.
        payload:  JSON body to send.

    Returns:
        dict: Parsed JSON response from the API.

    Raises:
        requests.HTTPError: On non-2xx response after all retries.
    """
    # PY-SEC-005: SSL verification always enabled
    response = requests.post(
        api_url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def deploy_application(env: str, version: str, dry_run: bool = False) -> None:
    """
    Deploy the application to the target environment via the deploy API.

    Args:
        env:     Target environment name.
        version: Application version string to deploy.
        dry_run: If True, log intent without making API calls.
    """
    enable_protection = env in PROTECTED_ENVS  # PY-HARD-003: no hardcoded env names

    if dry_run:
        logger.info(f"[DRY RUN] Would deploy version {version} to {env} "
                    f"(protection={'on' if enable_protection else 'off'})")
        return

    # PY-LINT-009: no imports inside functions
    # PY-SEC-004: no eval() — use yaml.safe_load
    # PY-SEC-007: yaml.safe_load not yaml.load
    config_response = requests.get(
        f"{API_URL}/config/{env}",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
        timeout=30,
    )
    config_response.raise_for_status()
    config = yaml.safe_load(config_response.text)   # safe — no arbitrary objects

    try:
        result = call_deploy_api(
            f"{API_URL}/deploy",
            API_TOKEN,
            {"env": env, "version": version, "config": config},
        )
        logger.info(f"Deploy API response: status={result.get('status')}")

    except requests.RequestException as e:
        logger.error(f"Deploy API call failed for env={env} version={version}: {e}",
                     exc_info=True)   # PY-ERR-007: exc_info included
        raise


# ── Database migration (PY-SEC-009, PY-ERR-009) ───────────────────────────────
def run_database_migration(version: str, dry_run: bool = False) -> None:
    """
    Apply pending database migrations for the given version.

    Args:
        version: Application version whose migrations to apply.
        dry_run: If True, log intent without executing migrations.

    Raises:
        ValueError:  If version is None or empty.
        RuntimeError: If the migration subprocess fails.
    """
    # PY-ERR-009: specific exception types, not Exception
    if not version:
        raise ValueError("version cannot be None or empty")

    if dry_run:
        logger.info(f"[DRY RUN] Would apply database migrations for version {version}")
        return

    # PY-SEC-009: parameterised query — no string formatting
    with sqlite3.connect(f"postgresql://{DB_HOST}:{DB_PORT}/{os.environ['DB_NAME']}") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO migrations (version, applied_at) VALUES (?, CURRENT_TIMESTAMP)"
            " ON CONFLICT (version) DO NOTHING",
            (version,),
        )
        conn.commit()
        logger.info(f"Migration recorded for version {version}")


# ── Snapshot cleanup (PY-IDEM-003, PY-ERR-003) ────────────────────────────────
def delete_old_snapshots(cutoff_date: str, dry_run: bool = False) -> None:
    """
    Delete EC2 snapshots older than the given cutoff date.
    Idempotent — handles already-deleted snapshots gracefully.

    Args:
        cutoff_date: ISO date string. Snapshots started before this are deleted.
        dry_run:     If True, log what would be deleted without deleting.
    """
    ec2 = boto3.client("ec2", region_name=REGION)

    try:
        snapshots = ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
    except ClientError as e:
        logger.error(f"Failed to list snapshots: {e}", exc_info=True)
        raise

    old_snapshots = [
        s for s in snapshots
        if s["StartTime"].strftime("%Y-%m-%d") < cutoff_date
    ]

    logger.info(f"Found {len(old_snapshots)} snapshots older than {cutoff_date}")

    for snapshot in old_snapshots:
        snap_id = snapshot["SnapshotId"]

        if dry_run:
            logger.info(f"[DRY RUN] Would delete snapshot: {snap_id}")
            continue

        try:
            ec2.delete_snapshot(SnapshotId=snap_id)
            logger.info(f"Deleted snapshot: {snap_id}")
        except ClientError as e:
            # PY-IDEM-003: gracefully handle already-deleted
            if e.response["Error"]["Code"] == "InvalidSnapshot.NotFound":
                logger.warning(f"Snapshot already deleted: {snap_id} — skipping")
            else:
                logger.error(f"Failed to delete snapshot {snap_id}: {e}", exc_info=True)
                raise


# ── Token generation (PY-SEC-006) ─────────────────────────────────────────────
def generate_deploy_token() -> str:
    """
    Generate a cryptographically secure random deployment token.

    Returns:
        str: 32-character URL-safe token.
    """
    # PY-SEC-006: secrets module for cryptographic randomness
    return secrets.token_urlsafe(32)


# ── Subprocess wrapper (PY-SEC-003, PY-ERR-004) ───────────────────────────────
def run_script(script_content: str, env_name: str) -> None:
    """
    Write a shell script to a secure temporary file and execute it.

    Args:
        script_content: Shell script body to execute.
        env_name:       Environment label used in logging.

    Raises:
        subprocess.CalledProcessError: If the script exits non-zero.
    """
    # PY-SEC-010: tempfile module — not /tmp/ string concatenation
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as tmp:
        tmp.write(script_content)
        tmp_path = tmp.name

    try:
        # PY-ERR-005: context manager (handled by with above)
        # PY-SEC-003: shell=False — arguments as list
        # PY-ERR-004: check=True — raises on non-zero exit
        subprocess.run(["bash", tmp_path], check=True, timeout=300)
        logger.info(f"Script executed successfully for env={env_name}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Permission check (PY-SEC-011) ─────────────────────────────────────────────
def check_permissions(user_role: str) -> None:
    """
    Validate that the caller has the required role to run a deployment.

    Args:
        user_role: Role string from the authentication context.

    Raises:
        PermissionError: If the role is not authorised for deployment.
    """
    # PY-SEC-011: explicit if/raise, not assert (assert is stripped with -O flag)
    allowed_roles = os.environ.get("DEPLOY_ALLOWED_ROLES", "admin,deployer").split(",")
    if user_role not in allowed_roles:
        raise PermissionError(
            f"Role '{user_role}' is not authorised to deploy. "
            f"Allowed roles: {allowed_roles}"
        )
    logger.info(f"Permission check passed for role: {user_role}")


# ── Main pipeline (PY-LINT-006, PY-LINT-007, PY-ERR-001, PY-ERR-006) ─────────
def main() -> None:
    """
    Entry point for the deployment pipeline.
    Parses arguments, runs all steps with checkpoint tracking,
    and exits non-zero on failure.
    """
    # PY-LINT-006: argparse — not sys.argv indexing
    parser = argparse.ArgumentParser(
        description="Infrastructure deployment pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env",      required=True,  help="Target environment (e.g. prod, staging)")
    parser.add_argument("--version",  required=True,  help="Application version to deploy (e.g. 1.4.2)")
    parser.add_argument("--dry-run",  action="store_true", help="Log what would happen without making changes")
    parser.add_argument("--cutoff",   default="2024-01-01", help="Snapshot cutoff date (YYYY-MM-DD)")
    args = parser.parse_args()

    # PY-IDEM-005: state tracking for checkpoint/resume
    state_file = Path(f".deploy_state_{args.env}.json")
    state = load_state(state_file)

    logger.info(f"Starting deployment: env={args.env} version={args.version} "
                f"dry_run={args.dry_run}")

    try:
        check_permissions(os.environ.get("DEPLOY_ROLE", "viewer"))

        # Step 1: provision S3 bucket
        if "bucket" not in state:
            create_s3_bucket(BACKUP_BUCKET, REGION, args.env, args.dry_run)
            state["bucket"] = True
            save_state(state_file, state)

        # Step 2: deploy application
        if "deploy" not in state:
            deploy_application(args.env, args.version, args.dry_run)
            state["deploy"] = True
            save_state(state_file, state)

        # Step 3: database migration
        if "migration" not in state:
            run_database_migration(args.version, args.dry_run)
            state["migration"] = True
            save_state(state_file, state)

        # Step 4: cleanup old snapshots
        if "cleanup" not in state:
            delete_old_snapshots(args.cutoff, args.dry_run)
            state["cleanup"] = True
            save_state(state_file, state)

        logger.info("Deployment pipeline completed successfully")
        state_file.unlink(missing_ok=True)   # clean up state on success

    except PermissionError as e:
        # PY-ERR-001: specific exception, not bare except
        logger.error(f"Authorisation failed: {e}")
        sys.exit(1)    # PY-ERR-006: always exit non-zero on fatal failure

    except ClientError as e:
        logger.error(f"AWS API error: {e}", exc_info=True)  # PY-ERR-007: exc_info
        sys.exit(1)

    except subprocess.CalledProcessError as e:
        logger.error(f"Script execution failed (exit {e.returncode}): {e.cmd}")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error in deployment pipeline: {e}", exc_info=True)
        sys.exit(1)


# PY-LINT-007: if __name__ == "__main__" guard
if __name__ == "__main__":
    main()
