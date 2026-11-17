#!/usr/bin/env python3
"""Check the layout of the skills this repository ships (make verify-skills).

- A skill is skills/<name>/SKILL.md. A SKILL.md anywhere else fails: a skill
  source listed without a path is scanned whole and would offer it.
- Its YAML frontmatter parses to a mapping whose `name` is the directory name,
  the name the default agent's values pin it by (agents/default-agent/values.yaml).

What the agent runtime accepts beyond that (fields, lengths) is the runtime's
own check; this script does not copy it.
"""

import pathlib
import sys

import yaml

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


def check_skill(directory: pathlib.Path) -> list[str]:
    skill_md = directory / "SKILL.md"
    try:
        fm = frontmatter(skill_md.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        return [f"{skill_md}: {exc}"]
    if fm.get("name") != directory.name:
        return [f"{skill_md}: name {fm.get('name')!r} is not the directory name {directory.name!r}"]
    return []


def main() -> int:
    skills = sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))
    if not skills:
        print("no skills found under skills/", file=sys.stderr)
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
