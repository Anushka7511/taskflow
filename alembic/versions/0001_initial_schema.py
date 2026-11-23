"""initial schema: users, projects, memberships, tasks

Revision ID: 0001
Revises: 
Create Date: 2026-11-14 08:36:56.248709
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_users_email_lowercase')),
    sa.CheckConstraint('length(trim(name)) > 0', name=op.f('ck_users_name_not_blank')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name='uq_users_email')
    )
    op.create_table('projects',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(trim(name)) > 0', name=op.f('ck_projects_name_not_blank')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_projects_owner_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects'))
    )
    op.create_index('ix_projects_owner_id', 'projects', ['owner_id'], unique=False)
    op.create_table('project_memberships',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.Enum('OWNER', 'ADMIN', 'MEMBER', name='project_role'), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_project_memberships_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_project_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_project_memberships')),
    sa.UniqueConstraint('project_id', 'user_id', name='uq_project_memberships_project_user')
    )
    op.create_index('ix_project_memberships_user_id', 'project_memberships', ['user_id'], unique=False)
    op.create_table('tasks',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('assigned_to', sa.Uuid(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('TODO', 'IN_PROGRESS', 'DONE', name='task_status'), server_default='TODO', nullable=False),
    sa.Column('priority', sa.Enum('LOW', 'MEDIUM', 'HIGH', name='task_priority'), server_default='MEDIUM', nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(trim(title)) > 0', name=op.f('ck_tasks_title_not_blank')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_tasks_version_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_tasks_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id', 'assigned_to'], ['project_memberships.project_id', 'project_memberships.user_id'], name='fk_tasks_assignee_is_project_member', ondelete='SET NULL (assigned_to)'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_tasks_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks'))
    )
    op.create_index('ix_tasks_assigned_to', 'tasks', ['assigned_to'], unique=False)
    op.create_index('ix_tasks_project_id_created_at', 'tasks', ['project_id', 'created_at'], unique=False)
    op.create_index('ix_tasks_project_id_due_date', 'tasks', ['project_id', 'due_date'], unique=False)
    op.create_index('ix_tasks_project_id_status', 'tasks', ['project_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tasks_project_id_status', table_name='tasks')
    op.drop_index('ix_tasks_project_id_due_date', table_name='tasks')
    op.drop_index('ix_tasks_project_id_created_at', table_name='tasks')
    op.drop_index('ix_tasks_assigned_to', table_name='tasks')
    op.drop_table('tasks')
    op.drop_index('ix_project_memberships_user_id', table_name='project_memberships')
    op.drop_table('project_memberships')
    op.drop_index('ix_projects_owner_id', table_name='projects')
    op.drop_table('projects')
    op.drop_table('users')
    # Native enum types are not dropped by drop_table; remove them explicitly.
    op.execute('DROP TYPE IF EXISTS task_priority')
    op.execute('DROP TYPE IF EXISTS task_status')
    op.execute('DROP TYPE IF EXISTS project_role')
