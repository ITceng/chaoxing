-- Generated from unified_system.models; review migrations before production use.

CREATE TABLE permissions (
	id VARCHAR(32) NOT NULL,
	name VARCHAR(80) NOT NULL,
	description VARCHAR(200) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE platforms (
	id VARCHAR(32) NOT NULL,
	key VARCHAR(48) NOT NULL,
	name VARCHAR(100) NOT NULL,
	code_available BOOLEAN NOT NULL,
	integration_status VARCHAR(24) NOT NULL,
	auth_methods JSON NOT NULL,
	capabilities JSON NOT NULL,
	last_verified_at TIMESTAMP WITHOUT TIME ZONE,
	status_note TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (key)
);

CREATE TABLE roles (
	id VARCHAR(32) NOT NULL,
	name VARCHAR(40) NOT NULL,
	description VARCHAR(200) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE users (
	id VARCHAR(32) NOT NULL,
	email VARCHAR(320) NOT NULL,
	display_name VARCHAR(120) NOT NULL,
	password_hash VARCHAR(512) NOT NULL,
	status VARCHAR(24) NOT NULL,
	auth_version INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	last_login_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE INDEX ix_users_status ON users (status);

CREATE TABLE admin_user_scopes (
	admin_id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (admin_id, user_id),
	CONSTRAINT ck_admin_scope_not_self CHECK (admin_id != user_id),
	FOREIGN KEY(admin_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE ai_provider_configs (
	id VARCHAR(32) NOT NULL,
	owner_user_id VARCHAR(32),
	provider VARCHAR(48) NOT NULL,
	endpoint VARCHAR(500) NOT NULL,
	model VARCHAR(160) NOT NULL,
	secret_ciphertext TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ai_owner_provider UNIQUE (owner_user_id, provider),
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_provider_configs_owner_user_id ON ai_provider_configs (owner_user_id);

CREATE TABLE ai_usage_logs (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	provider VARCHAR(48) NOT NULL,
	model VARCHAR(160) NOT NULL,
	purpose VARCHAR(48) NOT NULL,
	status VARCHAR(24) NOT NULL,
	input_tokens INTEGER NOT NULL,
	output_tokens INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_usage_logs_created_at ON ai_usage_logs (created_at);

CREATE INDEX ix_ai_usage_logs_user_id ON ai_usage_logs (user_id);

CREATE TABLE audit_logs (
	id VARCHAR(32) NOT NULL,
	actor_user_id VARCHAR(32),
	target_user_id VARCHAR(32),
	action VARCHAR(80) NOT NULL,
	entity_type VARCHAR(48) NOT NULL,
	entity_id VARCHAR(64) NOT NULL,
	metadata_json JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(target_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_logs_action ON audit_logs (action);

CREATE INDEX ix_audit_logs_actor_user_id ON audit_logs (actor_user_id);

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE INDEX ix_audit_logs_target_user_id ON audit_logs (target_user_id);

CREATE TABLE invitations (
	id VARCHAR(32) NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	code_hint VARCHAR(12) NOT NULL,
	created_by VARCHAR(32) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	max_uses INTEGER NOT NULL,
	used_count INTEGER NOT NULL,
	revoked_at TIMESTAMP WITHOUT TIME ZONE,
	note VARCHAR(200) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_invitation_usage_range CHECK (max_uses >= 1 AND used_count >= 0 AND used_count <= max_uses),
	UNIQUE (code_hash),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_invitations_expires_at ON invitations (expires_at);

CREATE TABLE login_logs (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32),
	identifier VARCHAR(320) NOT NULL,
	remote_fingerprint VARCHAR(64) NOT NULL,
	result VARCHAR(24) NOT NULL,
	user_agent VARCHAR(256) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_login_logs_created_at ON login_logs (created_at);

CREATE INDEX ix_login_logs_identifier ON login_logs (identifier);

CREATE INDEX ix_login_logs_remote_fingerprint ON login_logs (remote_fingerprint);

CREATE INDEX ix_login_logs_result ON login_logs (result);

CREATE INDEX ix_login_logs_user_id ON login_logs (user_id);

CREATE TABLE password_reset_tokens (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	created_by VARCHAR(32),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	used_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (token_hash),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_password_reset_tokens_expires_at ON password_reset_tokens (expires_at);

CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id);

CREATE TABLE platform_accounts (
	id VARCHAR(32) NOT NULL,
	platform_id VARCHAR(32) NOT NULL,
	account_mode VARCHAR(16) NOT NULL,
	label VARCHAR(120) NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_platform_account_id_platform UNIQUE (id, platform_id),
	CONSTRAINT uq_platform_account_id_mode UNIQUE (id, account_mode),
	CONSTRAINT ck_platform_account_mode CHECK (account_mode IN ('personal', 'shared')),
	CONSTRAINT ck_platform_account_status CHECK (status IN ('unverified', 'active', 'disabled', 'expired', 'error')),
	FOREIGN KEY(platform_id) REFERENCES platforms (id) ON DELETE RESTRICT
);

CREATE INDEX ix_platform_accounts_platform_id ON platform_accounts (platform_id);

CREATE INDEX ix_platform_accounts_status ON platform_accounts (status);

CREATE TABLE role_permissions (
	role_id VARCHAR(32) NOT NULL,
	permission_id VARCHAR(32) NOT NULL,
	PRIMARY KEY (role_id, permission_id),
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE,
	FOREIGN KEY(permission_id) REFERENCES permissions (id) ON DELETE CASCADE
);

CREATE TABLE user_roles (
	user_id VARCHAR(32) NOT NULL,
	role_id VARCHAR(32) NOT NULL,
	PRIMARY KEY (user_id, role_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
);

CREATE TABLE user_sessions (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	auth_version INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	last_seen_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITHOUT TIME ZONE,
	remote_fingerprint VARCHAR(64) NOT NULL,
	user_agent VARCHAR(256) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (token_hash)
);

CREATE INDEX ix_user_sessions_expires_at ON user_sessions (expires_at);

CREATE INDEX ix_user_sessions_user_active ON user_sessions (user_id, revoked_at, expires_at);

CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);

CREATE TABLE worker_instances (
	id VARCHAR(32) NOT NULL,
	platform_id VARCHAR(32) NOT NULL,
	worker_id VARCHAR(120) NOT NULL,
	version VARCHAR(80) NOT NULL,
	status VARCHAR(24) NOT NULL,
	capabilities JSON NOT NULL,
	last_seen_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(platform_id) REFERENCES platforms (id) ON DELETE RESTRICT,
	UNIQUE (worker_id)
);

CREATE INDEX ix_worker_instances_last_seen_at ON worker_instances (last_seen_at);

CREATE INDEX ix_worker_instances_platform_id ON worker_instances (platform_id);

CREATE INDEX ix_worker_instances_status ON worker_instances (status);

CREATE TABLE invitation_uses (
	id VARCHAR(32) NOT NULL,
	invitation_id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	used_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_invitation_user_use UNIQUE (invitation_id, user_id),
	FOREIGN KEY(invitation_id) REFERENCES invitations (id) ON DELETE RESTRICT,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_invitation_uses_invitation_id ON invitation_uses (invitation_id);

CREATE INDEX ix_invitation_uses_user_id ON invitation_uses (user_id);

CREATE TABLE personal_platform_accounts (
	account_id VARCHAR(32) NOT NULL,
	account_mode VARCHAR(16) NOT NULL,
	owner_user_id VARCHAR(32) NOT NULL,
	platform_id VARCHAR(32) NOT NULL,
	account_hint VARCHAR(120) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (account_id),
	CONSTRAINT fk_personal_account_platform FOREIGN KEY(account_id, platform_id) REFERENCES platform_accounts (id, platform_id) ON DELETE CASCADE,
	CONSTRAINT fk_personal_account_mode FOREIGN KEY(account_id, account_mode) REFERENCES platform_accounts (id, account_mode) ON DELETE CASCADE,
	CONSTRAINT ck_personal_account_mode CHECK (account_mode = 'personal'),
	CONSTRAINT uq_personal_account_owner UNIQUE (account_id, owner_user_id),
	CONSTRAINT uq_personal_owner_platform_hint UNIQUE (owner_user_id, platform_id, account_hint),
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_personal_platform_accounts_owner_user_id ON personal_platform_accounts (owner_user_id);

CREATE TABLE platform_account_secrets (
	id VARCHAR(32) NOT NULL,
	account_id VARCHAR(32) NOT NULL,
	created_by VARCHAR(32),
	auth_type VARCHAR(32) NOT NULL,
	ciphertext TEXT NOT NULL,
	version INTEGER NOT NULL,
	status VARCHAR(24) NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_account_secret_version UNIQUE (account_id, version),
	FOREIGN KEY(account_id) REFERENCES platform_accounts (id) ON DELETE CASCADE,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_account_secret_active_version ON platform_account_secrets (account_id, status, version);

CREATE INDEX ix_platform_account_secrets_account_id ON platform_account_secrets (account_id);

CREATE INDEX ix_platform_account_secrets_status ON platform_account_secrets (status);

CREATE TABLE shared_platform_accounts (
	account_id VARCHAR(32) NOT NULL,
	account_mode VARCHAR(16) NOT NULL,
	created_by VARCHAR(32) NOT NULL,
	account_hint VARCHAR(120) NOT NULL,
	is_open BOOLEAN NOT NULL,
	available_from TIMESTAMP WITHOUT TIME ZONE,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	max_concurrency INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (account_id),
	CONSTRAINT fk_shared_account_mode FOREIGN KEY(account_id, account_mode) REFERENCES platform_accounts (id, account_mode) ON DELETE CASCADE,
	CONSTRAINT ck_shared_account_mode CHECK (account_mode = 'shared'),
	CONSTRAINT ck_shared_account_validity CHECK (expires_at IS NULL OR available_from IS NULL OR expires_at > available_from),
	CONSTRAINT ck_shared_account_concurrency CHECK (max_concurrency >= 1 AND max_concurrency <= 16),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_shared_platform_accounts_created_by ON shared_platform_accounts (created_by);

CREATE INDEX ix_shared_platform_accounts_expires_at ON shared_platform_accounts (expires_at);

CREATE INDEX ix_shared_platform_accounts_is_open ON shared_platform_accounts (is_open);

CREATE TABLE account_access_grants (
	id VARCHAR(32) NOT NULL,
	shared_account_id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	granted_by VARCHAR(32) NOT NULL,
	valid_from TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	revoked_at TIMESTAMP WITHOUT TIME ZONE,
	scope JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_grant_account_user UNIQUE (id, shared_account_id, user_id),
	CONSTRAINT ck_grant_validity_range CHECK (expires_at IS NULL OR expires_at > valid_from),
	FOREIGN KEY(shared_account_id) REFERENCES shared_platform_accounts (account_id) ON DELETE RESTRICT,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(granted_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_account_access_grants_expires_at ON account_access_grants (expires_at);

CREATE INDEX ix_account_access_grants_revoked_at ON account_access_grants (revoked_at);

CREATE INDEX ix_account_access_grants_shared_account_id ON account_access_grants (shared_account_id);

CREATE INDEX ix_account_access_grants_user_id ON account_access_grants (user_id);

CREATE INDEX ix_grant_account_user_revoked ON account_access_grants (shared_account_id, user_id, revoked_at);

CREATE UNIQUE INDEX uq_active_account_user_grant ON account_access_grants (shared_account_id, user_id) WHERE revoked_at IS NULL;

CREATE TABLE tasks (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	personal_account_id VARCHAR(32),
	shared_account_id VARCHAR(32),
	access_grant_id VARCHAR(32),
	kind VARCHAR(48) NOT NULL,
	status VARCHAR(24) NOT NULL,
	request_data JSON NOT NULL,
	idempotency_key VARCHAR(128),
	timeout_seconds INTEGER NOT NULL,
	attempt INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	worker_id VARCHAR(120),
	lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
	cancel_requested_at TIMESTAMP WITHOUT TIME ZONE,
	result_summary JSON NOT NULL,
	error_code VARCHAR(64),
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT ck_task_exactly_one_account_mode CHECK ((personal_account_id IS NOT NULL AND shared_account_id IS NULL AND access_grant_id IS NULL) OR (personal_account_id IS NULL AND shared_account_id IS NOT NULL AND access_grant_id IS NOT NULL)),
	CONSTRAINT ck_task_timeout_range CHECK (timeout_seconds >= 1 AND timeout_seconds <= 86400),
	CONSTRAINT ck_task_status CHECK (status IN ('queued', 'leased', 'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled', 'timed_out')),
	CONSTRAINT ck_task_attempts CHECK (attempt >= 0 AND max_attempts >= 1),
	CONSTRAINT uq_tasks_id_user UNIQUE (id, user_id),
	CONSTRAINT uq_task_user_idempotency UNIQUE (user_id, idempotency_key),
	CONSTRAINT uq_task_shared_owner_grant UNIQUE (id, user_id, shared_account_id, access_grant_id),
	CONSTRAINT fk_task_personal_owner FOREIGN KEY(personal_account_id, user_id) REFERENCES personal_platform_accounts (account_id, owner_user_id) ON DELETE RESTRICT,
	CONSTRAINT fk_task_shared_account FOREIGN KEY(shared_account_id) REFERENCES shared_platform_accounts (account_id) ON DELETE RESTRICT,
	CONSTRAINT fk_task_shared_grant_owner FOREIGN KEY(access_grant_id, shared_account_id, user_id) REFERENCES account_access_grants (id, shared_account_id, user_id) ON DELETE RESTRICT,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_tasks_created_at ON tasks (created_at);

CREATE INDEX ix_tasks_lease_expires_at ON tasks (lease_expires_at);

CREATE INDEX ix_tasks_queue ON tasks (status, created_at);

CREATE INDEX ix_tasks_status ON tasks (status);

CREATE INDEX ix_tasks_user_created ON tasks (user_id, created_at);

CREATE INDEX ix_tasks_user_id ON tasks (user_id);

CREATE INDEX ix_tasks_worker_id ON tasks (worker_id);

CREATE TABLE account_execution_leases (
	account_id VARCHAR(32) NOT NULL,
	slot_no INTEGER NOT NULL,
	task_id VARCHAR(32),
	lease_token_hash VARCHAR(64),
	worker_id VARCHAR(120),
	acquired_at TIMESTAMP WITHOUT TIME ZONE,
	heartbeat_at TIMESTAMP WITHOUT TIME ZONE,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (account_id, slot_no),
	FOREIGN KEY(account_id) REFERENCES platform_accounts (id) ON DELETE CASCADE,
	UNIQUE (task_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE RESTRICT
);

CREATE INDEX ix_account_execution_leases_expires_at ON account_execution_leases (expires_at);

CREATE TABLE account_usage_logs (
	id VARCHAR(32) NOT NULL,
	shared_account_id VARCHAR(32) NOT NULL,
	grant_id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	task_id VARCHAR(32) NOT NULL,
	event_type VARCHAR(48) NOT NULL,
	details JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT fk_usage_grant_owner FOREIGN KEY(grant_id, shared_account_id, user_id) REFERENCES account_access_grants (id, shared_account_id, user_id) ON DELETE RESTRICT,
	CONSTRAINT fk_usage_task_owner FOREIGN KEY(task_id, user_id, shared_account_id, grant_id) REFERENCES tasks (id, user_id, shared_account_id, access_grant_id) ON DELETE RESTRICT,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_account_usage_logs_created_at ON account_usage_logs (created_at);

CREATE INDEX ix_account_usage_logs_grant_id ON account_usage_logs (grant_id);

CREATE INDEX ix_account_usage_logs_shared_account_id ON account_usage_logs (shared_account_id);

CREATE INDEX ix_account_usage_logs_task_id ON account_usage_logs (task_id);

CREATE INDEX ix_account_usage_logs_user_id ON account_usage_logs (user_id);

CREATE INDEX ix_usage_account_created ON account_usage_logs (shared_account_id, created_at);

CREATE TABLE task_events (
	id VARCHAR(32) NOT NULL,
	task_id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	event_type VARCHAR(48) NOT NULL,
	details JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT fk_task_event_owner FOREIGN KEY(task_id, user_id) REFERENCES tasks (id, user_id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_task_events_created_at ON task_events (created_at);

CREATE INDEX ix_task_events_task_created ON task_events (task_id, created_at);

CREATE INDEX ix_task_events_task_id ON task_events (task_id);

CREATE INDEX ix_task_events_user_id ON task_events (user_id);
