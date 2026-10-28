-- Cover foreign keys used by deletes, joins, and hosted workflow reconciliation.
create index calculations_run on public.calculations(run_id);
create index calculations_revision on public.calculations(revision_id);
create index feedback_project on public.feedback(project_id);
create index feedback_revision on public.feedback(revision_id);
create index feedback_run on public.feedback(run_id);
create index projects_current_revision on public.projects(current_revision_id);
create index resource_usage_run on public.resource_usage(run_id);
create index revisions_restored_from on public.revisions(restored_from);
create index runs_base_revision on public.runs(base_revision_id);
create index runs_owner on public.runs(owner_id);
