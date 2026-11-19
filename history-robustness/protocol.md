# History robustness using the saved archive

Frozen before computing this pass's new subgroup results on 2026-11-22.
This is a post-hoc descriptive analysis of previously examined records, not
prospective registration or an independent evaluation set. The manuscript's
94/163 collision count, 4/158 versus 2,845/2,865 comparison, and prior audit's
exclusion of the two largest error-source archives were known before this plan.

## Question and alternatives

The question is whether the complete-export collision and history-coverage
findings are driven by one error-source model, repeated wrong address, task, or
successful comparison model. Removing a dominant source or balancing these
units could substantially weaken the results. These checks cannot establish
transfer to a different benchmark, natural wrong-coworker frequency, or an
effect of asking an agent to change its own behavior.

## Fixed inputs and selection

Read the saved per-action records, correction records, export-control records,
and all saved history replays. Do not use aggregate summary files as answers.
Reconstruct the current manuscript cohort using valid, first-eligible-call
placements in fully WorkBench-correct donor episodes, excluding the previously
examined GPT-4.1 donor. Keep all original wrong actions for collision analyses.
Hash every input and retain mismatches or missing records as failures.

Independently reconcile action identities, committed destinations, checker
judgments, the number and validity of permitted corrections, and the two SDKs'
whole-export equality results. Check raw versus alias decisions and unmodified
successful-history decisions. If saved records do not establish a particular
claim, state the resulting verification boundary.

## Estimands

For original error e, let O_e be the original lookup checker's error indicator,
and let H_e be the fraction of its eligible successful donor histories in which
the same checker flags the inserted destination. The primary history contrast
is mean_e(H_e - O_e), giving each matched original error one vote. Report raw
placement counts separately because the same error and donor call can recur.
For all original errors, let C_e indicate that every permitted correction
produces an identical complete canonical-alias export.

In addition to error-weighted means, report macro means by task, error-source
model, erroneous episode, and exact committed wrong destination. Within each
group average over its original errors first, then give groups equal weight.
These estimands deliberately describe different distributions over this fixed
archive. They are not estimates under an identified deployment sampling law.

For collisions, report the same group-weighted mean of C_e. For history
comparisons, use the same matched error set and weights for H_e and O_e.
Destinations are lowercased committed strings, retaining the simulator's two
compound-string destinations as distinct values.

## Concentration checks and reporting

1. Reproduce the two largest error-source archive exclusion from the prior audit.
2. Compute exhaustive leave-one-group-out estimates for task, request template,
   error-source model, and exact wrong destination. Report the extrema, removed
   group and remaining error denominator. Report all group details in CSV.
3. Remove each successful donor model in turn, recomputing H_e. Keep track of
   any error losing all eligible donors. No bootstrap, p-values, or population
   confidence intervals will be generated from repeated templates.
4. Count distinct modified donor calls and average one verdict per exact
   (donor model, task, call position, wrong destination) to expose replay reuse.
   This modified-call mean has a different denominator from the original-error
   mean and will be labeled accordingly.
5. Assess whether the existing controls can defeat the particular explanation
   they address. A new lookup-deletion/insertion test that merely restates the
   checker definition is not an independent outcome test. Do not add it to
   inflate evidence. Flag any meaningful unsupported claim that needs a new
   setting or naturally occurring failures instead.

If a reasonable balancing or deletion check reverses the history contrast,
concentrates collisions in one group, or reveals unmodified-success false
positives, report it and narrow the interpretation. Otherwise stop after these
checks. Provide one concise manuscript paragraph and an operational use of the
existing comparison method. No model calls, paid inference, local models,
external sends, new architectures, or edits outside this folder.
