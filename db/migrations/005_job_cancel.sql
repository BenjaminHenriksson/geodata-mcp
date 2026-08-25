-- analyze(op='cancel'): a queued job can be marked 'cancelled' before the
-- worker claims it (the claim query only takes status='queued', so a
-- cancelled job is never picked up). Running jobs are not interruptible.
ALTER TABLE app.jobs DROP CONSTRAINT jobs_status_check;
ALTER TABLE app.jobs ADD CONSTRAINT jobs_status_check
  CHECK (status IN ('queued','running','done','error','cancelled'));
