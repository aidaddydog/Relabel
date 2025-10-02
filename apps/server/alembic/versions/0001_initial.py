
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('admin_users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(length=64), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(length=512), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index(op.f('ix_admin_users_username'), 'admin_users', ['username'], unique=True)

    op.create_table('client_auth',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('description', sa.String(length=255), nullable=False, server_default=""),
        sa.Column('code_hash', sa.String(length=512), nullable=False),
        sa.Column('code_plain', sa.String(length=6), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used', sa.DateTime(), nullable=True),
    )
    op.create_index(op.f('ix_client_auth_is_active'), 'client_auth', ['is_active'], unique=False)

    op.create_table('meta_kv',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('k', sa.String(length=128), nullable=False, unique=True),
        sa.Column('v', sa.Text(), nullable=False, server_default=""),
        sa.Column('remark', sa.String(length=255), nullable=False, server_default="")
    )
    op.create_index(op.f('ix_meta_kv_k'), 'meta_kv', ['k'], unique=True)

    op.create_table('tracking_file',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tracking_no', sa.String(length=64), nullable=False, unique=True),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.Column('print_status', sa.String(length=16), nullable=False, server_default="not_printed"),
        sa.Column('print_count', sa.Integer(), nullable=False, server_default="0"),
        sa.Column('first_print_time', sa.DateTime(), nullable=True),
        sa.Column('last_print_time', sa.DateTime(), nullable=True),
        sa.Column('last_print_client_name', sa.String(length=255), nullable=True),
    )
    op.create_index(op.f('ix_tracking_file_tracking_no'), 'tracking_file', ['tracking_no'], unique=True)
    op.create_index(op.f('ix_tracking_file_uploaded_at'), 'tracking_file', ['uploaded_at'], unique=False)

    op.create_table('print_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('access_code', sa.String(length=6), nullable=False),
        sa.Column('order_id', sa.String(length=128), nullable=True),
        sa.Column('tracking_no', sa.String(length=64), nullable=False),
        sa.Column('result', sa.String(length=32), nullable=False),
        sa.Column('host', sa.String(length=128), nullable=True),
        sa.Column('user', sa.String(length=128), nullable=True),
        sa.Column('client_version', sa.String(length=64), nullable=True),
        sa.Column('printer_name', sa.String(length=128), nullable=True),
        sa.Column('mac_list', sa.Text(), nullable=True),
        sa.Column('ip_list', sa.Text(), nullable=True),
        sa.Column('pdf_sha256', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index(op.f('ix_print_events_created_at'), 'print_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_print_events_tracking_no'), 'print_events', ['tracking_no'], unique=False)

    op.create_table('order_mapping',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_id', sa.String(length=128), nullable=False),
        sa.Column('tracking_no', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index(op.f('ix_order_mapping_order_id'), 'order_mapping', ['order_id'], unique=False)
    op.create_index(op.f('ix_order_mapping_tracking_no'), 'order_mapping', ['tracking_no'], unique=False)

def downgrade():
    op.drop_table('order_mapping')
    op.drop_table('print_events')
    op.drop_table('tracking_file')
    op.drop_table('meta_kv')
    op.drop_table('client_auth')
    op.drop_table('admin_users')
