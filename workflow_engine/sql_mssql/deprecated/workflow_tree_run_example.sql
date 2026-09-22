/*
  Workflow Engine (wf schema) - End-to-end run example for DelphiTreeFlow.

  Prerequisites:
  - Run workflow_tree_seed_example.sql
  - A registered worker exists in wf.worker and has a valid token in wf.worker_token
  - wf worker procedures are deployed (sp_worker_request_task/sp_worker_submit_result/sp_start_workflow_instance)
*/

SET NOCOUNT ON;
GO

DECLARE @workflow_name NVARCHAR(256) = N'DelphiTreeFlow';
DECLARE @wid BIGINT = 1;                   -- TODO: set to existing wf.worker.id
DECLARE @tok NVARCHAR(4000) = N'<secret>'; -- TODO: set to matching bearer token secret

IF @tok = N'<secret>'
BEGIN
    RAISERROR(N'Set @wid and @tok before running this script.', 16, 1);
    RETURN;
END

DECLARE @ver_id BIGINT =
(
    SELECT TOP (1) wv.id
    FROM wf.workflow_version AS wv
    INNER JOIN wf.workflow_def AS wd ON wd.id = wv.workflow_def_id
    WHERE wd.name = @workflow_name
    ORDER BY wv.id DESC
);

IF @ver_id IS NULL
BEGIN
    RAISERROR(N'Workflow definition "%s" not found. Run workflow_tree_seed_example.sql first.', 16, 1, @workflow_name);
    RETURN;
END

DECLARE @instance_context NVARCHAR(MAX) = N'{"sampleId":"S-001","mode":2,"shouldRunQc":1,"hasMorePages":1}';
DECLARE @instance_id BIGINT;

INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json)
VALUES (@ver_id, N'CREATED', CAST(@instance_context AS json));

SET @instance_id = SCOPE_IDENTITY();
EXEC wf.sp_start_workflow_instance @workflow_instance_id = @instance_id;

PRINT CONCAT(N'Started instance ', @instance_id, N' for workflow ', @workflow_name, N'.');

DECLARE @accepted BIT;
DECLARE @st VARCHAR(32);
DECLARE @nr INT;
DECLARE @idle_loops INT = 0;
DECLARE @max_loops INT = 500;
DECLARE @loop_no INT = 0;

WHILE @loop_no < @max_loops
BEGIN
    SET @loop_no += 1;

    CREATE TABLE #claim
    (
      node_execution_id BIGINT,
      workflow_instance_id BIGINT,
      node_key NVARCHAR(128),
      action_name NVARCHAR(256),
      capability NVARCHAR(128),
      attempt_no INT,
      input_json NVARCHAR(MAX),
      iteration_no INT
    );

    INSERT INTO #claim
    EXEC wf.sp_worker_request_task
      @worker_id = @wid,
      @worker_token = @tok,
      @capability = NULL,
      @max_lease_seconds = 300;

    IF NOT EXISTS (SELECT 1 FROM #claim)
    BEGIN
      DROP TABLE #claim;
      SET @idle_loops += 1;

      IF EXISTS (
          SELECT 1
          FROM wf.workflow_instance
          WHERE id = @instance_id AND status IN (N'COMPLETED', N'FAILED', N'CANCELLED')
      )
          BREAK;

      IF @idle_loops > 20
      BEGIN
          PRINT N'No claims for prolonged period; stopping polling loop.';
          BREAK;
      END

      WAITFOR DELAY '00:00:00.200';
      CONTINUE;
    END

    SET @idle_loops = 0;

    DECLARE @ne BIGINT = (SELECT TOP (1) node_execution_id FROM #claim);
    DECLARE @nk NVARCHAR(128) = (SELECT TOP (1) node_key FROM #claim);
    DECLARE @iter INT = (SELECT TOP (1) iteration_no FROM #claim);
    DECLARE @rc INT;
    DECLARE @out NVARCHAR(MAX);

    /*
      Deterministic result-code policy:
      - load_input: 1        -> IF takes THEN branch
      - fetch_page: 1 then 0 -> WHILE runs 2 iterations then exits
      - all others: 1        -> success path
    */
    SET @rc = CASE
        WHEN @nk = N'load_input' THEN 1
        WHEN @nk = N'fetch_page' AND ISNULL(@iter, 1) = 1 THEN 1
        WHEN @nk = N'fetch_page' AND ISNULL(@iter, 1) >= 2 THEN 0
        ELSE 1
      END;

    SET @out = CONCAT(
      N'{"ok":true,"nodeKey":"', @nk,
      N'","resultCode":', CAST(@rc AS NVARCHAR(32)),
      N',"iterationNo":', CAST(ISNULL(@iter, 0) AS NVARCHAR(32)),
      N'}'
    );

    EXEC wf.sp_worker_submit_result
      @node_execution_id = @ne,
      @worker_id = @wid,
      @worker_token = @tok,
      @result_code = @rc,
      @output_json = @out,
      @accepted = @accepted OUTPUT,
      @instance_status = @st OUTPUT,
      @next_ready_count = @nr OUTPUT;

    PRINT CONCAT(
      N'node=', @nk,
      N' exec=', @ne,
      N' iter=', ISNULL(@iter, 0),
      N' rc=', @rc,
      N' accepted=', IIF(@accepted = 1, N'1', N'0'),
      N' status=', ISNULL(@st, N'?'),
      N' nextReady=', ISNULL(CAST(@nr AS NVARCHAR(32)), N'?')
    );

    DROP TABLE #claim;

    IF @st IN (N'COMPLETED', N'FAILED', N'CANCELLED')
      BREAK;
END

SELECT
  wi.id AS workflow_instance_id,
  wi.status AS instance_status,
  wi.started_at_utc,
  wi.completed_at_utc
FROM wf.workflow_instance AS wi
WHERE wi.id = @instance_id;

SELECT
  ne.id AS node_execution_id,
  wn.node_key,
  ne.status,
  ne.result_code,
  ne.iteration_no,
  ne.input_json,
  ne.output_json
FROM wf.node_execution AS ne
INNER JOIN wf.workflow_node AS wn ON wn.id = ne.workflow_node_id
WHERE ne.workflow_instance_id = @instance_id
ORDER BY ne.id;
GO

