"""The frontend's `ErrorCode` union must name exactly what the server emits.

This is the same shape of guard as `REQUIRED_CLAIMS` being asserted against
Google's advertised `claims_supported`: a cross-boundary agreement that no
type checker sees, because one side is Python and the other is TypeScript.

It is here rather than in prose because drift had already happened in both
directions at once. The union carried nine codes from the removed buying flow
(`SOLD_OUT`, `HOLD_EXPIRED`, `UTR_ALREADY_USED`…) that the server had deleted,
and was missing `UNKNOWN_PASS`, which the server does emit and which the
scanner screen branches on by name. The second is the dangerous half: it
compiled only because `ErrorDetail.code` is widened to `ErrorCode | (string &
{})`, so the union had quietly stopped being a contract and become decoration.

At a gate the code is what decides whether a volunteer waves somebody through,
sends them to the desk, or calls security. A union that does not match the
server is a screen that cannot branch on a refusal it is being told about.
"""
import re
from pathlib import Path

#: `apps/api` is mounted at /srv/app in the container and is the repo's own
#: apps/api on a laptop, so the server side is found relative to this file
#: either way.
API_ROOT = Path(__file__).resolve().parents[1]
PY_ERRORS = API_ROOT / "app" / "core" / "errors.py"

#: The frontend is not part of the API image. docker-compose mounts its source
#: read-only at /srv/web-src purely so this check can see both sides; outside
#: the container it sits beside apps/api.
TS_CANDIDATES = (
    Path("/srv/web-src/lib/types.ts"),
    API_ROOT.parent / "web" / "src" / "lib" / "types.ts",
)


def _ts_types() -> Path | None:
    return next((p for p in TS_CANDIDATES if p.exists()), None)


def _ts_error_codes() -> set[str]:
    path = _ts_types()
    assert path is not None, "frontend types.ts not reachable"
    source = path.read_text()
    start = source.index("export type ErrorCode =")
    return set(re.findall(r'"([A-Z_]+)"', source[start : source.index(";", start)]))


def _server_error_codes() -> set[str]:
    source = PY_ERRORS.read_text()
    start = source.index("class ErrorCode")
    rest = source[start:]
    end = rest.index("\nclass ") if "\nclass " in rest else len(rest)
    return set(re.findall(r'^\s+([A-Z_]+)\s*=\s*"', rest[:end], re.M))


def test_the_frontend_names_every_code_the_server_can_send():
    """Missing codes are the dangerous direction: the UI cannot branch on a
    refusal it has no name for, and the widened `code` type hides it."""
    missing = _server_error_codes() - _ts_error_codes()
    assert not missing, f"types.ts is missing codes the server emits: {sorted(missing)}"


def test_the_frontend_names_no_code_the_server_cannot_send():
    """Extra codes are dead branches — and they read as live features to
    whoever opens the file next."""
    phantom = _ts_error_codes() - _server_error_codes()
    assert not phantom, f"types.ts names codes the server cannot emit: {sorted(phantom)}"


def test_the_check_can_actually_see_both_sides():
    """Guards against the two ways this file could pass while proving nothing:
    a regex that silently matches nothing, or a frontend it cannot reach."""
    assert _ts_types() is not None, (
        "types.ts not found — the docker-compose read-only mount of "
        "apps/web/src is what makes this check possible; without it these "
        "tests would pass by never running"
    )
    assert len(_server_error_codes()) > 10
    assert len(_ts_error_codes()) > 10
