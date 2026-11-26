"""Command line: `fhir-over-mcp serve` and `fhir-over-mcp cohort load`."""

from __future__ import annotations

import argparse
import sys

from fhir_over_mcp.config import load_fhir_settings


def _serve(args: argparse.Namespace) -> int:
    """Run the MCP server over stdio. Refuses to start off the allowlist (invariant 2)."""
    import logging

    from fhir_over_mcp.fhir.client import is_allowed
    from fhir_over_mcp.server.app import build_server
    from fhir_over_mcp.server.scope import PatientScope

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
    scope = None
    # `is not None`, so an empty `--patient` refuses to start rather than starting unbound.
    if args.patient is not None:
        try:
            scope = PatientScope(args.patient)
        except ValueError as e:
            print(f"refusing to start: --patient is {e}", file=sys.stderr)
            return 2
        logging.getLogger("fhir_over_mcp").info("patient-context mode: bound to %s", scope.ref)
    build_server(settings, scope=scope).run()
    return 0


def _cohort_load(args: argparse.Namespace) -> int:
    from synthetic_cohort import bundle_dir

    from fhir_over_mcp.cohort.load import load_cohort

    base = args.fhir_base or load_fhir_settings().base_url
    count = load_cohort(base, bundle_dir())
    print(f"loaded {count} bundles into {base}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fhir-over-mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the MCP server over stdio")
    serve.add_argument("--fhir-base", help="FHIR base URL (must be on the allowlist)")
    serve.add_argument(
        "--patient", help="patient-context mode: bind to one patient, e.g. Patient/123"
    )
    serve.set_defaults(func=_serve)
    cohort = commands.add_parser("cohort", help="the pinned Synthea cohort").add_subparsers(
        dest="action", required=True
    )
    load = cohort.add_parser("load", help="load the bundles into a local FHIR server")
    load.add_argument("--fhir-base", help="defaults to the configured base URL")
    load.set_defaults(func=_cohort_load)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
