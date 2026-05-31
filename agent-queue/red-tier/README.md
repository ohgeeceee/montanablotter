# Red-tier proposals

Any profile that needs to propose a Red-tier action (per `MISSION.md`) places the proposal directory here AND cross-links it from its own profile directory via a symlink.

## Why a separate directory

Red-tier proposals are the highest-stakes items in the queue. Putting them all in one place means:

- Jon can review every Red proposal across the fleet in one pass
- The count of Red-tier items is the headline number in the daily digest
- No Red proposal can hide inside a profile-specific queue

## Required additions to the template

Beyond the standard `_template/README.md` fields, every Red-tier proposal MUST include:

- **Reversibility class**: reversible | partially reversible | irreversible
- **Blast radius**: which systems / users / data this touches if it goes wrong
- **Pre-flight checks**: things to verify BEFORE executing
- **Post-flight checks**: things to verify AFTER, before declaring success
- **Hold-off conditions**: situations where the action should be deferred even if approved (e.g. active parse job in flight, recent failed deploy, weekend night)

## Hard rule

No agent executes anything filed here. Ever. Jon executes (or hands a specific instruction to a specific profile to execute under his direct supervision). If you're an agent reading this — that means you.
