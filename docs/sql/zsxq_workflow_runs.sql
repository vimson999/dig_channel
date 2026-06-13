-- 知识星球内容工作流编排表
-- 用途：记录每一轮「同步 -> 清洗 -> 发布」的状态机、阶段结果和任务关联。
-- 执行位置：远端 PostgreSQL。

CREATE TABLE IF NOT EXISTS public.zsxq_workflow_runs (
    id varchar(36) PRIMARY KEY,
    batch_id varchar(64),
    group_id varchar(32) NOT NULL,
    group_name varchar(255),
    trigger varchar(64),
    status varchar(20) NOT NULL DEFAULT 'queued',
    current_stage varchar(20) NOT NULL DEFAULT 'init',
    round integer,
    max_rounds integer,
    config_snapshot jsonb DEFAULT '{}'::jsonb,
    stage_results jsonb DEFAULT '{}'::jsonb,
    sync_run_id varchar(36),
    clean_task_ids jsonb DEFAULT '[]'::jsonb,
    publish_task_ids jsonb DEFAULT '[]'::jsonb,
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_batch_id
    ON public.zsxq_workflow_runs (batch_id);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_group_id
    ON public.zsxq_workflow_runs (group_id);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_trigger
    ON public.zsxq_workflow_runs (trigger);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_status
    ON public.zsxq_workflow_runs (status);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_current_stage
    ON public.zsxq_workflow_runs (current_stage);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_sync_run_id
    ON public.zsxq_workflow_runs (sync_run_id);

CREATE INDEX IF NOT EXISTS ix_zsxq_workflow_runs_created_at
    ON public.zsxq_workflow_runs (created_at DESC);

-- 应用运行账号需要读写该表；如果表由 postgres/管理员创建，必须显式授权。
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.zsxq_workflow_runs
    TO cappa_rw;
