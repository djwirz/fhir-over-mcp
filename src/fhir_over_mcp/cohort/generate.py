"""Generate the pinned cohort with Synthea (ADR 0007). Only needed to change the cohort.

Synthea runs from its released jar inside a pinned JRE container, so no local Java is needed and
every run uses the same Java. The jar is downloaded once to .cache/ and its sha256 checked before
it's used.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from fhir_over_mcp.cohort import BUNDLE_DIR, COHORT_DIR
from fhir_over_mcp.cohort.manifest import write_manifest

SYNTHEA_VERSION = "4.0.0"
SYNTHEA_JAR_URL = (
    "https://github.com/synthetichealth/synthea/releases/download/"
    f"v{SYNTHEA_VERSION}/synthea-with-dependencies.jar"
)
SYNTHEA_JAR_SHA256 = "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
JRE_IMAGE = (
    "eclipse-temurin:21.0.12.1_1-jre-noble"
    "@sha256:cd35bb33859641c5374415826a5951845104093eaa401cbe9d3d9023444f1c13"
)
# Fixed so that ages, dates and "five years of history" don't move with the calendar.
REFERENCE_DATE = "20261101"
STATE = "Massachusetts"
CACHE_DIR = COHORT_DIR.parent / ".cache" / "synthea"


@dataclass(frozen=True)
class Run:
    name: str
    seed: int
    clinician_seed: int
    population: int
    keep_module: str | None


RUNS = (
    # A general population: non-respiratory patients for fact, list and absent cases.
    Run("general", seed=1122, clinician_seed=1122, population=15, keep_module=None),
    # Respiratory-enriched: only patients with active asthma or COPD at the reference date.
    Run("resp", seed=2211, clinician_seed=2211, population=20, keep_module="keep_respiratory.json"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_jar() -> Path:
    jar = CACHE_DIR / f"synthea-{SYNTHEA_VERSION}.jar"
    if not jar.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        partial = jar.with_suffix(".part")
        with (
            httpx.stream("GET", SYNTHEA_JAR_URL, follow_redirects=True, timeout=600) as r,
            partial.open("wb") as f,
        ):
            r.raise_for_status()
            for chunk in r.iter_bytes():
                f.write(chunk)
        partial.rename(jar)
    actual = sha256_file(jar)
    if actual != SYNTHEA_JAR_SHA256:
        jar.unlink()
        raise RuntimeError(f"Synthea jar sha256 mismatch: got {actual}, deleted it")
    return jar


def run_synthea(run: Run, jar: Path, out_dir: Path) -> None:
    cmd = [
        "docker", "run", "--rm",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp",
        "-v", f"{jar}:/synthea.jar:ro",
        "-v", f"{COHORT_DIR}:/cohort:ro",
        "-v", f"{out_dir}:/out",
        "-w", "/out",
        JRE_IMAGE,
        "java", "-jar", "/synthea.jar",
        "-s", str(run.seed),
        "-cs", str(run.clinician_seed),
        "-r", REFERENCE_DATE,
        # Stop the simulation at the reference date too. Without this Synthea runs to the wall
        # clock: records get events after the reference date, and an encounter still open at the
        # end gets "now" as its end time, so no two runs match.
        "-e", REFERENCE_DATE,
        "-p", str(run.population),
        "-c", "/cohort/synthea.properties",
        "--exporter.baseDirectory=/out/",
    ]  # fmt: skip
    if run.keep_module:
        cmd += ["-k", f"/cohort/{run.keep_module}"]
    cmd.append(STATE)
    subprocess.run(cmd, check=True)  # noqa: S603 - fixed argv, no shell


def collect(run: Run, out_dir: Path, dest: Path) -> None:
    """Copy one run's bundles into `dest`, prefixed by run name, with stable file names."""
    for src in sorted((out_dir / "fhir").glob("*.json")):
        name = src.name
        for shared in ("hospitalInformation", "practitionerInformation"):
            if name.startswith(shared):
                name = f"{shared}.json"  # Synthea appends a timestamp; drop it
        shutil.copyfile(src, dest / f"{run.name}-{name}")


def generate() -> None:
    jar = ensure_jar()
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)
    BUNDLE_DIR.mkdir(parents=True)
    for run in RUNS:
        with tempfile.TemporaryDirectory(prefix=f"synthea-{run.name}-") as tmp:
            out = Path(tmp)
            run_synthea(run, jar, out)
            collect(run, out, BUNDLE_DIR)
    write_manifest(
        {
            "synthea_version": SYNTHEA_VERSION,
            "synthea_jar_sha256": SYNTHEA_JAR_SHA256,
            "jre_image": JRE_IMAGE,
            "reference_date": REFERENCE_DATE,
            "state": STATE,
            "properties_sha256": sha256_file(COHORT_DIR / "synthea.properties"),
            "keep_module_sha256": sha256_file(COHORT_DIR / "keep_respiratory.json"),
            "runs": [run.__dict__ for run in RUNS],
        }
    )
