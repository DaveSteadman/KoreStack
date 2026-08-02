# CronPrompt Skill

## Purpose
Create, query, update, enable, disable, and delete scheduled cron prompts stored as JSON files in `controldata/schedules/`. Each CronPrompt defines a schedule and a prompt string that the scheduler runs automatically on each firing.

## Trigger keyword: cron prompt

## Interface
- Module: `KoreAgent/app/system_skills/CronPrompt/cronprompt_skill.py`
- Functions:
  - `cronprompt_list()`
  - `cronprompt_get(name: str)`
  - `cronprompt_create(name: str, schedule: str, prompt: str, output_template: str = "")`
  - `cronprompt_set_enabled(name: str, enabled: bool)`
  - `cronprompt_set_schedule(name: str, schedule: str)`
  - `cronprompt_set_prompt(name: str, prompt: str, output_template: str = "")`
  - `cronprompt_delete(name: str)`

## Tool Selection Guidance
- Use this skill for scheduled automation prompts that run on a clock.
- Prefer `cronprompt_list()` when the user asks what recurring automations are configured.
- Prefer this skill over the overloaded word `task` when the thing being managed is a scheduled prompt rather than a PlanTask.
