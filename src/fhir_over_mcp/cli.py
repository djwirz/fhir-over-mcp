"""Command line: `fhir-over-mcp cohort {generate,load,check}`. Later slices add more."""

from __future__ import annotations

import argparse
import sys

from fhir_over_mcp.config import load_fhir_settings


def _cohort_generate(_: argparse.Namespace) -> int:
    from fhir_over_mcp.cohort.generate import generate

    generate()
    return _cohort_check(argparse.Namespace())


def _cohort_load(args: argparse.Namespace) -> int:
    from fhir_over_mcp.cohort import BUNDLE_DIR
    from fhir_over_mcp.cohort.load import load_cohort

    base = args.fhir_base or load_fhir_settings().base_url
    count = load_cohort(base, BUNDLE_DIR)
    print(f"loaded {count} bundles into {base}")
    return 0


def _cohort_check(_: argparse.Namespace) -> int:
    from fhir_over_mcp.cohort.check import check

    for line in check().lines():
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fhir-over-mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    cohort = commands.add_parser("cohort", help="the pinned Synthea cohort").add_subparsers(
        dest="action", required=True
    )
    cohort.add_parser("generate", help="regenerate the bundles with Synthea (Docker)").set_defaults(
        func=_cohort_generate
    )
    load = cohort.add_parser("load", help="load the bundles into a local FHIR server")
    load.add_argument("--fhir-base", help="defaults to the configured base URL")
    load.set_defaults(func=_cohort_load)
    cohort.add_parser("check", help="summarise what the bundles contain").set_defaults(
        func=_cohort_check
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
