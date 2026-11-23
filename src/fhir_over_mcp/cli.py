"""Command line: `fhir-over-mcp serve` and `fhir-over-mcp cohort {generate,load,check}`."""

from __future__ import annotations

import argparse
import sys

from fhir_over_mcp.config import load_fhir_settings


def _serve(args: argparse.Namespace) -> int:
    """Run the MCP server over stdio. Refuses to start off the allowlist (invariant 2)."""
    import logging

    from fhir_over_mcp.fhir.client import is_allowed
    from fhir_over_mcp.server.app import build_server

    # stdout is the MCP wire; anything we say goes to stderr.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = load_fhir_settings()
    if args.fhir_base:
        settings = settings.model_copy(update={"base_url": args.fhir_base})
    if not is_allowed(settings.base_url, settings.allowlist):
        print(
            f"refusing to start: {settings.base_url} is not on the FHIR allowlist "
            f"({', '.join(settings.allowlist)})",
            file=sys.stderr,
        )
        return 2
    build_server(settings).run()
    return 0


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
    serve = commands.add_parser("serve", help="run the MCP server over stdio")
    serve.add_argument("--fhir-base", help="FHIR base URL (must be on the allowlist)")
    serve.set_defaults(func=_serve)
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
