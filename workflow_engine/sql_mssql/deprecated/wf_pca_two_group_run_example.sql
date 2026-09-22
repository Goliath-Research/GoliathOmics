/*
  Workflow Engine (wf schema) - End-to-end run example for PCaTwoGroupFlow.

  Simulates methyl-centroid and methyl-detector workers against the per-chromosome
  fan-out tree seeded by wf_pca_two_group_seed.sql.

  Prerequisites:
  - wf schema + wf_scope_variables.sql
  - wf_sql_runtime_parity.sql, wf_sql_branch_parity.sql, wf_sql_scope_writepath_parity.sql
  - wf_pca_two_group_seed.sql
  - Registered worker in wf.worker with valid token in wf.worker_token

  Set @wid and @tok before running.
*/

SET NOCOUNT ON;
GO

DECLARE @workflow_name NVARCHAR(256) = N'PCaTwoGroupFlow';
DECLARE @wid BIGINT = 1;
DECLARE @tok NVARCHAR(4000) = N'<secret>';

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
    RAISERROR(N'Workflow "%s" not found. Run wf_pca_two_group_seed.sql first.', 16, 1, @workflow_name);
    RETURN;
END

DECLARE @instance_context NVARCHAR(MAX) = N'{
  "projectPath": "/home/ubuntu/Work/prostate-cancer/configs/project_PCa3.json",
  "context": "CG",
  "centroid1Dir": "/work/projects/prostate-cancer/PCa3/centroids/controls/healthy/all",
  "centroid2Dir": "/work/projects/prostate-cancer/PCa3/centroids/diseases/cancer/PCa_Low",
  "detectOutDir": "/work/projects/prostate-cancer/PCa3/detections/all/PCa_Low",
  "group1Label": "group1",
  "group2Label": "group2"
}';

DECLARE @instance_id BIGINT;

INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json)
VALUES (@ver_id, N'CREATED', CAST(@instance_context AS json));

SET @instance_id = SCOPE_IDENTITY();

INSERT INTO wf.instance_cursor (workflow_instance_id, last_polled_at_utc, notes)
VALUES (@instance_id, NULL, N'PCaTwoGroupFlow run example');

EXEC wf.sp_start_workflow_instance @workflow_instance_id = @instance_id;

PRINT CONCAT(N'Started instance ', @instance_id, N' for workflow ', @workflow_name, N'.');

DECLARE @accepted BIT;
DECLARE @st VARCHAR(32);
DECLARE @nr INT;
DECLARE @idle_loops INT = 0;
DECLARE @max_loops INT = 5000;
DECLARE @loop_no INT = 0;
DECLARE @tasks_done INT = 0;

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

      IF @idle_loops > 50
      BEGIN
          PRINT N'No claims for prolonged period; stopping polling loop.';
          BREAK;
      END

      WAITFOR DELAY '00:00:00.050';
      CONTINUE;
    END

    SET @idle_loops = 0;

    DECLARE @ne BIGINT = (SELECT TOP (1) node_execution_id FROM #claim);
    DECLARE @nk NVARCHAR(128) = (SELECT TOP (1) node_key FROM #claim);
    DECLARE @cap NVARCHAR(128) = (SELECT TOP (1) capability FROM #claim);
    DECLARE @inp NVARCHAR(MAX) = (SELECT TOP (1) input_json FROM #claim);
    DECLARE @chr NVARCHAR(8);
    DECLARE @rc INT = 0;
    DECLARE @out NVARCHAR(MAX);

    SET @chr = JSON_VALUE(@inp, N'$.chromosome');

    IF @cap = N'methyl-centroid'
    BEGIN
        SET @out = CONCAT(
          N'{"ok":true,"h5":"',
          JSON_VALUE(@inp, N'$.outputDir'),
          N'/', @chr, N'-',
          JSON_VALUE(@inp, N'$.context'),
          N'.h5","chromosome":"', @chr, N'"}'
        );
    END
    ELSE IF @cap = N'methyl-detector'
    BEGIN
        DECLARE @nDmps INT = ABS(CHECKSUM(@nk)) % 1000 + 1;
        SET @out = CONCAT(
          N'{"ok":true,"dmpsCsv":"',
          JSON_VALUE(@inp, N'$.outputDir'),
          N'/dmps-', @chr, N'.csv","chromosome":"', @chr,
          N'","nDmps":', CAST(@nDmps AS NVARCHAR(32)), N'}'
        );
    END
    ELSE
    BEGIN
        SET @rc = -1;
        SET @out = CONCAT(N'{"ok":false,"nodeKey":"', @nk, N'"}');
    END

    EXEC wf.sp_worker_submit_result
      @node_execution_id = @ne,
      @worker_id = @wid,
      @worker_token = @tok,
      @result_code = @rc,
      @output_json = @out,
      @accepted = @accepted OUTPUT,
      @instance_status = @st OUTPUT,
      @next_ready_count = @nr OUTPUT;

    SET @tasks_done += 1;

    IF @tasks_done <= 10 OR @cap = N'methyl-detector' OR @st IN (N'COMPLETED', N'FAILED')
    BEGIN
        PRINT CONCAT(
          N'node=', @nk,
          N' cap=', @cap,
          N' exec=', @ne,
          N' rc=', @rc,
          N' accepted=', IIF(@accepted = 1, N'1', N'0'),
          N' status=', ISNULL(@st, N'?'),
          N' nextReady=', ISNULL(CAST(@nr AS NVARCHAR(32)), N'?')
        );
    END

    DROP TABLE #claim;

    IF @st IN (N'COMPLETED', N'FAILED', N'CANCELLED')
      BREAK;
END

PRINT CONCAT(N'Worker loop finished after ', @tasks_done, N' task submissions.');

/* --- Assertions --- */

DECLARE @inst_status NVARCHAR(32);
SELECT @inst_status = status FROM wf.workflow_instance WHERE id = @instance_id;

IF @inst_status <> N'COMPLETED'
BEGIN
    RAISERROR(N'ASSERT FAILED: instance status expected COMPLETED, got %s', 16, 1, @inst_status);
END
ELSE
    PRINT N'ASSERT OK: instance COMPLETED.';

DECLARE @detect_ok INT =
(
    SELECT COUNT(*)
    FROM wf.node_execution AS ne
    INNER JOIN wf.workflow_node AS wn ON wn.id = ne.workflow_node_id
    WHERE ne.workflow_instance_id = @instance_id
      AND wn.node_key LIKE N'detect[_]%'
      AND ne.status = N'SUCCEEDED'
);

IF @detect_ok <> 24
BEGIN
    RAISERROR(N'ASSERT FAILED: expected 24 SUCCEEDED detect_* nodes, got %d', 16, 1, @detect_ok);
END
ELSE
    PRINT N'ASSERT OK: 24 detect_* nodes SUCCEEDED.';

DECLARE @centroid_ok INT =
(
    SELECT COUNT(*)
    FROM wf.node_execution AS ne
    INNER JOIN wf.workflow_node AS wn ON wn.id = ne.workflow_node_id
    WHERE ne.workflow_instance_id = @instance_id
      AND wn.node_key LIKE N'centroid_g%'
      AND ne.status = N'SUCCEEDED'
);

IF @centroid_ok <> 48
BEGIN
    RAISERROR(N'ASSERT FAILED: expected 48 SUCCEEDED centroid nodes, got %d', 16, 1, @centroid_ok);
END
ELSE
    PRINT N'ASSERT OK: 48 centroid nodes SUCCEEDED (24 x group1 + 24 x group2).';

DECLARE @ordering_violations INT =
(
    SELECT COUNT(*)
    FROM wf.node_execution AS det
    INNER JOIN wf.workflow_node AS wn_det ON wn_det.id = det.workflow_node_id
    CROSS APPLY (
        SELECT RIGHT(wn_det.node_key, LEN(wn_det.node_key) - LEN(N'detect_')) AS chr_suffix
    ) AS sfx
    INNER JOIN wf.workflow_node AS wn_c1 ON wn_c1.node_key = CONCAT(N'centroid_g1_', sfx.chr_suffix)
    INNER JOIN wf.node_execution AS c1
        ON c1.workflow_node_id = wn_c1.id AND c1.workflow_instance_id = @instance_id
    INNER JOIN wf.workflow_node AS wn_c2 ON wn_c2.node_key = CONCAT(N'centroid_g2_', sfx.chr_suffix)
    INNER JOIN wf.node_execution AS c2
        ON c2.workflow_node_id = wn_c2.id AND c2.workflow_instance_id = @instance_id
    WHERE det.workflow_instance_id = @instance_id
      AND wn_det.node_key LIKE N'detect[_]%'
      AND det.started_at_utc IS NOT NULL
      AND c1.ended_at_utc IS NOT NULL
      AND c2.ended_at_utc IS NOT NULL
      AND (
          det.started_at_utc < c1.ended_at_utc
          OR det.started_at_utc < c2.ended_at_utc
      )
);

IF @ordering_violations > 0
BEGIN
    RAISERROR(N'ASSERT FAILED: %d detect_* nodes started before both centroids completed', 16, 1, @ordering_violations);
END
ELSE
    PRINT N'ASSERT OK: each detect_* started after both centroids for that chromosome.';

DECLARE @last_detect_n INT;
SELECT TOP (1) @last_detect_n = TRY_CAST(sv.value_json AS INT)
FROM wf.scope_variable AS sv
WHERE sv.workflow_instance_id = @instance_id
  AND sv.var_name = N'lastDetectN'
ORDER BY sv.updated_at_utc DESC, sv.scope_node_execution_id DESC;

IF @last_detect_n IS NULL OR @last_detect_n < 1
BEGIN
    RAISERROR(N'ASSERT FAILED: lastDetectN output binding not written to wf.scope_variable (deploy wf_sql_scope_writepath_parity.sql)', 16, 1);
END
ELSE
    PRINT CONCAT(N'ASSERT OK: lastDetectN=', @last_detect_n, N' from detect_* output_path binding.');

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
  ne.started_at_utc,
  ne.ended_at_utc,
  LEFT(ne.input_json, 120) AS input_preview
FROM wf.node_execution AS ne
INNER JOIN wf.workflow_node AS wn ON wn.id = ne.workflow_node_id
WHERE ne.workflow_instance_id = @instance_id
  AND wn.node_type = N'ACTION'
ORDER BY ne.id;
GO
