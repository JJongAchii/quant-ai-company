# ADR-0030: Bound maintenance evidence without stalling observation

Status: Accepted (2026-09-21)

## Context

The maintenance worker stopped reaching model analysis after the repository archive grew from
8,259,245 bytes to 9,595,486 bytes, beyond its 8 MiB download limit. The stored error collapsed
the safe `archive_too_large` cause into `current_repository_unavailable`. Earlier triage jobs also
exceeded the 88,000-character provider input limit because source excerpts were bounded but live
system records were not. An errored `review` or `triage` job then prevented fresh observations from
being placed in a durable job.

Raising both limits would postpone the same failure as repository evidence and system history grow.
The database already stores an exact prior repository snapshot, and Git blob identifiers are
content-addressed.

## Decision

Repository diagnosis reads the current Git tree and reuses each prior allowlisted text file only
when its computed blob SHA matches the current entry. It fetches only changed or missing readable
blobs and retains the existing per-file and aggregate source limits. Full repository archives remain
limited to 32 MiB for reviewed release extraction; they are no longer the diagnosis input path.

Provider input continues to be built from a deep copy of durable evidence. If source excerpt
reduction is insufficient, nested system strings and record lists receive progressively smaller
prompt-only limits with explicit omission counts. Exact commits, configuration digests and citable
system keys remain. The full records stay unchanged in PostgreSQL.

Safe GitHub error codes are preserved in repository evidence and the durable job error. An errored
review/triage head no longer blocks all collection: ready jobs retain priority, while at most four
active observation jobs may be buffered. Explicit owner review jobs keep their existing priority.

## Consequences

The first observation without a prior snapshot can require one Git blob request per readable file;
later observations normally fetch only changed blobs. The ten-minute Temporal activity limit and
GitHub installation quota bound that bootstrap. If four jobs are already waiting, later records stay
unconsumed in their source tables until capacity returns.

Prompt compaction can omit detail from the model call, so the maintainer must use its existing
bounded inspection round when the retained evidence is insufficient. Omission markers prevent it
from treating absent prompt detail as evidence that functionality is absent.
