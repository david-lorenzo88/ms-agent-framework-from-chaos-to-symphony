# Working on this repo

## Check whether the PR is merged before adding commits to the branch

**Before committing or pushing to a feature branch, fetch and check whether its
pull request has already been merged.**

```bash
git fetch origin --prune
git log --oneline origin/main..origin/<branch>   # anything here is NOT on main
```

A merged PR is finished. Pushing to its branch afterwards does not reopen it and
does not add the commit to `main` — the work lands nowhere and looks done. This
has happened three times here: PRs 1, 7 and 8 each merged while a later commit
sat on the branch unnoticed.

When the PR is already merged, restart the branch from the latest `main` and
rebase any unmerged commits onto it, then open a **new** PR:

```bash
git fetch origin main
git rebase origin/main            # keeps commits main does not have
git push --force-with-lease
```

Say plainly which commits a merge actually took when reporting one as merged,
rather than assuming the whole branch went in.

## Checking a change before pushing

Three commands, in this order. All three must be clean.

```bash
python -m ruff check src scripts deck
CHAOS_PROVIDER=offline python scripts/smoke.py   # 12/12 + DevUI input, endings, agents
python scripts/drive.py                          # needs the site running; 12/12 clean
```

`scripts/smoke.py` carries three checks that exist because each guards a claim
nothing else would catch: what DevUI asks for before running a pattern, whether
every example prompt still reaches the ending it advertises, and whether the
Agents panel can still read each agent's prompt out of the built workflow.

## Offline is not a stand-in for a live model

`ScriptedChatClient` reads the in-memory store directly, so it answers from real
shipment data an actual model would never have been given. A pattern can pass
every offline check and still be broken against Foundry or Azure OpenAI.

Before changing an agent, ask what it could know from the prompt alone. An agent
whose output steers the graph needs its own tool to fetch the facts; an agent
late in a chain usually does not, because the agents before it put their
findings in the shared conversation.
