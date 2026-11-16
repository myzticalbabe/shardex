# STEM learning guide — shardex

SHARDEX is a shard execution engine: it ranks and runs shards, and produces mesh scenes from the results.

This guide is for teachers, mentors, club leaders, parents and students who want to
use this repository as a vehicle for real STEM learning — in particular for girls and
other groups still under-represented in computing, engineering and mathematics. It
says what the project actually teaches, how to run a session with it, and what to
remove so that nobody is excluded by accident.

## What this project teaches

**Subject areas**

- scheduling
- ranking algorithms
- graph structures
- reproducible results

**Skills practised**

- defining a ranking and defending it
- reproducing a result from a stated configuration
- separating a tie from a win

## Prerequisites

Python 3.10 or newer. Fixtures are included, but some tests expect a sibling checkout layout — see the note in the session plan.

Nothing else is assumed for the entry-level activity below: no prior programming
experience, no particular hardware, and no home internet connection.

## Six practices this repository is set up to support

These follow the published guidance on gender-equitable STEM education — connect the
work to learners' lives, let them investigate real problems, treat struggle as part of
the work, name and challenge stereotypes, make the work collaborative, and put
learners in front of diverse role models.

1. **Connect it to their lives.** any leaderboard: a school competition, a game ranking, a recommendation feed
2. **Let them investigate.** ask the group to write a ranking rule for something they care about, then find the input that makes it unfair
3. **Make struggle normal.** Build the session so that the first attempt is *expected*
   to fail. Say out loud that debugging is the subject, not a penalty. Ask "what did
   that error tell you?" before offering the answer.
4. **Challenge the stereotype.** Name who does this work and why it is not a gendered
   skill. Praise the method ("you isolated the variable"), not the person ("you're a
   natural") — the second kind of praise teaches learners that ability is fixed.
5. **Make it social.** pairs design a ranking, another pair tries to game it
6. **Show diverse role models.** algorithm designers and the researchers who documented how ranking systems fail

## A 90-minute session

1. *(15 min)* Run the engine on the fixture and read the ranking.
2. *(25 min)* Find one input where the ranking is obviously wrong.
3. *(35 min)* Change the rule so that case is handled, without breaking the golden file.
4. *(15 min)* Argue for or against the change as a group.

## Vocabulary to define before the session

Specialist vocabulary is a hidden entry test if nobody explains it. Define each of
these the first time it appears, in one sentence, in the learners' own words:

1. **shard** — one unit of execution.
2. **rank** — an ordering derived from scores.
3. **golden file** — a saved expected output used as a test.

## Accessibility

Read [ACCESSIBILITY.md](ACCESSIBILITY.md) before running a session. Two rules matter
most in a mixed-ability room:

- Every activity must be completable with a keyboard alone, with a visible focus ring.
- Every instruction names the control or the heading ("the **Run** button in the
  toolbar"), never a colour or a position.

Pair learners rather than singling anyone out, and always offer a text-only route for
a visual step.

## Contributing as a learner

Small, well-scoped contributions are the point, not a consolation prize:

- Browse issues labelled
  [`good first issue`](https://github.com/myzticalbabe/shardex/labels/good%20first%20issue).
- Read [CONTRIBUTING.md](CONTRIBUTING.md) — it has a section written for first-time
  contributors.
- Ask in [SUPPORT.md](SUPPORT.md) if anything is unclear. Asking is expected here; it
  is not a failure.

## Licence and classroom reuse

Classroom, club and workshop use is welcome. See `LICENSE` for the exact terms, and
check whether your institution requires a separate agreement before redistributing
modified copies.
