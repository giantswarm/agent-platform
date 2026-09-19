"""Flux's semver, offline: the subset of Masterminds/semver v3 (what
source-controller evaluates OCIRepository.spec.ref.semver with) a
components.<name>.versionRange needs — parse, precedence, constraint check —
so the render assertions can say which published tags a range admits without
a cluster, and resolve a range against a registry's tag list the way Flux does.

The rules that matter for the ranges in values.yaml (all Masterminds, none npm):

- a prerelease compares identifier by identifier (SemVer 2.0 §11): numeric
  identifiers by value, alphanumeric ones lexically, numeric below
  alphanumeric, a shorter list of identifiers below a longer one with the same
  prefix; a version without a prerelease outranks every prerelease of its core;
- a range none of whose comparators carries a prerelease skips prerelease
  versions entirely (">=1.0.0 <1.1.0" matches no 1.0.x-anything), and ONE
  comparator with a prerelease — a "-0" bound — switches prerelease evaluation
  on for the whole AND group (">=1.0.0 <1.1.0-0" admits 1.0.1-dev.x; verified
  against Masterminds/semver v3.5.0, giantswarm/agent-platform#608), which is
  why a stable range carries no -0 anywhere and a dev-channel range carries one;
- a prerelease is confined to no patch tuple: ">=1.0.0-0 <1.1.0-0" admits
  1.0.1-dev.x as well; a ceiling holds the patch only when it names it;
- a bare version is an exact match (`=`), `1.x` / `0.2.x` are minor / patch
  ranges without prerelease bounds, comparators are AND-ed by space or comma
  and OR-ed by `||`; Masterminds rejects `*-*`.

Deliberately stdlib-only: the CI image has no PyYAML and no semver package.
"""

import re

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_COMPARATOR = re.compile(r"^(>=|<=|!=|==|>|<|=|~|\^)?\s*v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


class Version:
    __slots__ = ("core", "prerelease", "text")

    def __init__(self, text: str):
        m = _VERSION.match(text)
        if not m:
            raise ValueError(f"not a semver: {text!r}")
        self.text = text
        self.core = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        self.prerelease = m.group(4) or ""

    def key(self):
        """A sort key with SemVer 2.0 precedence."""
        if not self.prerelease:
            return (self.core, 1, ())
        ids = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in self.prerelease.split("."))
        return (self.core, 0, ids)

    def __lt__(self, other):
        return self.key() < other.key()

    def __eq__(self, other):
        return self.key() == other.key()

    def __repr__(self):
        return self.text


def parse(text: str):
    """Version or None for a tag that is not a semver (registry noise such as
    sha256-….sbom or artifacthub.io)."""
    try:
        return Version(text)
    except ValueError:
        return None


def _comparators(term: str):
    """The (op, Version) pairs one AND-ed constraint term expands to."""
    m = _COMPARATOR.match(term.strip())
    if not m:
        raise ValueError(f"not a Masterminds constraint: {term!r}")
    op, major, minor, patch, pre = m.groups()
    wild = [p for p in (major, minor, patch) if p is not None and not p.isdigit()]
    if wild or minor is None or patch is None:
        # x-range / partial: 1.x, 0.2.x, 1, 1.2 — a floor and a ceiling on the
        # next identifier up, both without a prerelease.
        if op not in (None, "=", "=="):
            raise ValueError(f"wildcard with an operator is not supported here: {term!r}")
        if not major.isdigit():
            return []  # "x" / "*": everything (that is not a prerelease)
        lo = [int(major), 0, 0]
        if minor is not None and minor.isdigit():
            lo[1] = int(minor)
            hi = (lo[0], lo[1] + 1, 0)
        else:
            hi = (lo[0] + 1, 0, 0)
        return [(">=", Version("%d.%d.%d" % tuple(lo))), ("<", Version("%d.%d.%d" % hi))]
    v = Version(f"{major}.{minor}.{patch}" + (f"-{pre}" if pre else ""))
    if op in (None, "=", "=="):
        return [("=", v)]
    if op == "~":  # ~1.2.3 → >=1.2.3 <1.3.0
        return [(">=", v), ("<", Version("%d.%d.0" % (v.core[0], v.core[1] + 1)))]
    if op == "^":  # ^1.2.3 → >=1.2.3 <2.0.0 (^0.x.y → <0.(x+1).0)
        hi = (v.core[0] + 1, 0, 0) if v.core[0] else (0, v.core[1] + 1, 0)
        return [(">=", v), ("<", Version("%d.%d.%d" % hi))]
    return [(op, v)]


def _check(op: str, v: Version, c: Version) -> bool:
    if op in ("=", "=="):
        return v == c
    if op == "!=":
        return not v == c
    if op == ">":
        return c < v
    if op == ">=":
        return not v < c
    if op == "<":
        return v < c
    if op == "<=":
        return not c < v
    raise ValueError(op)


def satisfies(version: str, constraint: str) -> bool:
    """Whether Flux would consider `version` for `constraint`."""
    v = Version(version)
    for alternative in constraint.split("||"):
        terms = [t for t in re.split(r"[\s,]+", alternative.strip()) if t]
        if not terms:
            continue
        comparators = [pair for t in terms for pair in _comparators(t)]
        # Masterminds: an AND group none of whose comparators carries a
        # prerelease skips prerelease versions; one that does evaluates them all.
        if v.prerelease and not any(c.prerelease for _, c in comparators):
            continue
        if all(_check(op, v, c) for op, c in comparators):
            return True
    return False


def resolve(tags, constraint: str):
    """The tag Flux picks: the highest semver among `tags` that satisfies the
    constraint (no semverFilter), or None."""
    matching = [t for t in tags if parse(t) is not None and satisfies(t, constraint)]
    return max(matching, key=lambda t: Version(t).key()) if matching else None


def base(version: str) -> str:
    """The X.Y.Z of a version, its prerelease dropped."""
    return "%d.%d.%d" % Version(version).core
