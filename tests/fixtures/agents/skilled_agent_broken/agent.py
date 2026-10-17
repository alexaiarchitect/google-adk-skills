"""Expense claim agent (fixture: skills done wrong, one problem per rule).

* `claim-notes`: a name with spaces and an 1,099-character description (AGT070);
* `empty-skill`: a directory with no SKILL.md (AGT070);
* `long-procedure`: a 600-line body (AGT071);
* `fx`: a 10-character description (AGT073), and the agent instruction copies
  its body word for word (AGT072);
* `SkillLifecycleConfig(max_active_skills=MAX_ACTIVE)` with `MAX_ACTIVE = 0`
  (AGT075), in BOUNDED mode with no ADK_ENABLE_SKILL_LIFECYCLE in a .env
  (AGT074).

It does not load: ADK raises on the first bad skill. That is the point.
"""

import pathlib

from google.adk.agents import Agent
from google.adk.skills import load_skill_from_dir, models
from google.adk.tools.skill_toolset import (
    SkillLifecycleConfig, SkillLifecycleMode, SkillToolset,
)

SKILLS_DIR = pathlib.Path(__file__).parent / "skills"
MAX_ACTIVE = 0

FX_STEPS = (
    "Convert every foreign-currency receipt to US dollars at the receipt date.\n"
    "Use the rate published by the finance team for that day, never a live rate.\n"
    "Round the converted amount to the cent before comparing it with the limit."
)

fx_skill = models.Skill(
    frontmatter=models.Frontmatter(name="fx", description="Converts."),
    instructions=FX_STEPS,
)

broken_skills = SkillToolset(
    skills=[
        fx_skill,
        load_skill_from_dir(SKILLS_DIR / "claim-notes"),
        load_skill_from_dir(SKILLS_DIR / "empty-skill"),
        load_skill_from_dir(SKILLS_DIR / "long-procedure"),
    ],
    lifecycle_config=SkillLifecycleConfig(
        default_mode=SkillLifecycleMode.BOUNDED, max_active_skills=MAX_ACTIVE,
    ),
)

root_agent = Agent(
    name="broken_skills_agent",
    model="gemini-flash-latest",
    instruction=(
        "You process expense claims.\n"
        "Convert every foreign-currency receipt to US dollars at the receipt date.\n"
        "Use the rate published by the finance team for that day, never a live rate.\n"
        "Round the converted amount to the cent before comparing it with the limit.\n"
        "If a claim needs a full manual review, load the long-procedure skill."
    ),
    tools=[broken_skills],
)
