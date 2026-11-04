#!/usr/bin/env python3
"""Validate every skill in this repository against what the Agent Platform's runtime accepts.

A skill is a directory of skills/ with a SKILL.md whose YAML frontmatter the kagent Go runtime
parses with the agentskills.io fields (https://agentskills.io/specification#frontmatter). A
SKILL.md that does not parse, or an unknown frontmatter field on an older runtime, fails the boot
of every agent that mounts the skill — so this check runs on every pull request (make verify-skills).

A SKILL.md anywhere else in the repository fails too: a skill source listed without a path
(the plain-URL form of a skill repository) is scanned whole, and would offer it as a skill.
"""

import pathlib
import re
import sys

import yaml

KNOWN_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


def frontmatter(text: str):
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter: SKILL.md must start with a '---' line")
    body = text[len("---\n"):]
    end = body.find("\n---\n")
    if end < 0 and not body.endswith("\n---"):
        raise ValueError("frontmatter is not closed with a '---' line")
    data = yaml.safe_load(body[:end] if end >= 0 else body[: -len("\n---")])
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    return data


def check_skill(directory: pathlib.Path):
    problems = []
    skill_md = directory / "SKILL.md"
    try:
        fm = frontmatter(skill_md.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        return [f"{skill_md}: {exc}"]

    unknown = set(fm) - KNOWN_FIELDS
    if unknown:
        problems.append(
            f"{skill_md}: unknown frontmatter field(s) {sorted(unknown)} -- only {sorted(KNOWN_FIELDS)} are read; "
            "a kagent line before 0.11.0-gs.11 fails the agent's boot on them"
        )
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        problems.append(f"{skill_md}: 'name' is required")
    else:
        if name != directory.name:
            # The agent's release mounts a skill under the name of its skills entry, so a
            # differing directory name works; it is still confusing for the wizard and readers.
            print(f"warning: {skill_md}: name {name!r} differs from the directory name {directory.name!r}", file=sys.stderr)
        if len(name) > MAX_NAME or not NAME_RE.match(name):
            problems.append(f"{skill_md}: name {name!r} must be lowercase letters, digits and single hyphens, at most {MAX_NAME} characters")
    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append(f"{skill_md}: 'description' is required")
    elif len(description) > MAX_DESCRIPTION:
        problems.append(f"{skill_md}: description is {len(description)} characters, the limit is {MAX_DESCRIPTION}")
    metadata = fm.get("metadata")
    if metadata is not None and (
        not isinstance(metadata, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items())
    ):
        problems.append(f"{skill_md}: 'metadata' must be a mapping of strings to strings")
    return problems


def main() -> int:
    skills = sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))
    if not skills:
        print("no skills found", file=sys.stderr)
        return 1
    problems = [
        f"{p}: a SKILL.md outside skills/<name>/ -- a scan of the whole repository would offer it as a skill"
        for p in sorted(ROOT.rglob("SKILL.md"))
        if ".git" not in p.relative_to(ROOT).parts and p.parent not in skills
    ]
    for directory in skills:
        problems.extend(check_skill(directory))
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(skills)} skills checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
