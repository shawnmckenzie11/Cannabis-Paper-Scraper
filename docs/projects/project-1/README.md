# Project 1 — Client literature scraper blueprint

Repeatable architecture and automation package for spinning up a new **clinical/preclinical literature intelligence system** in an adjacent research domain. Clones the proven Cannabis Paper Scraper stack (node tree, Maude + LLM, RL calibration, Cursor agents) without copying cannabis-specific cues or schema literals.

## Which document to read

| Audience | Start here |
| :-- | :-- |
| Client / domain expert | [`architecture-design-document.md`](architecture-design-document.md) — §2 Discovery, §4 routing, §10 worksheets |
| Engineering (new repo fork) | [`architecture-design-document.md`](architecture-design-document.md) §3.5 + [`client-config-template/`](client-config-template/) |
| Cursor agents (RL / calibration) | [`agent-runbook.md`](agent-runbook.md) first, then [`.cursor/rules/rl-calibration.mdc`](../../../.cursor/rules/rl-calibration.mdc) |
| Filter / search UI changes | [`.cursor/agents/filter-agent.md`](../../../.cursor/agents/filter-agent.md) + `python3 filter_agent.py` |
| Reference implementation detail | [`docs/agent_automation_plan.md`](../../agent_automation_plan.md) (cannabis production mapping) |

## Folder contents

```
project-1/
├── README.md                          ← this file
├── architecture-design-document.md    ← client-facing spec (signed at kickoff)
├── agent-runbook.md                   ← operator commands for agents
└── client-config-template/            ← empty stubs to fill per engagement
    ├── discovery-worksheet.md
    ├── rules_config.client.json
    └── subnode_field_scopes.client.py
```

## Typical engagement sequence

1. Fill discovery worksheet with client expert → populate config templates.
2. Client signs architecture doc (§10.4).
3. Fork repo, rename Fly app, copy filled configs into production filenames.
4. Run phased delivery (§9) with `calibration-automation` agent on node2 holdouts.
5. Hand off signed doc, configs, dashboard, and `handoff_learning_log.json`.

## Reference vs client-specific

| Keep from reference repo | Replace per client |
| :-- | :-- |
| `calibration_agent.py`, RL scripts, agents | `rules_config.json` cues |
| Node 0→1B→1A→2A/2B/2C structure | `maude_cues.json` |
| Fly deploy + `/data/calibration_runs/` | `schema.sql` domain columns |
| Review queue API + feedback audit | `subnode_field_scopes.py` field lists |
| RL cycle (batch → patch → deploy → refresh) | Expert decision tree content |
