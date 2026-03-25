ADMIN_ACCESS_ROLES = ('super_admin', 'ops', 'editor', 'revenue', 'read_only')
ADMIN_MANAGEMENT_ROLES = ('super_admin',)
EMAIL_OPS_SEND_ROLES = ('super_admin', 'ops', 'revenue')
OPERATIONS_ROLES = ('super_admin', 'ops')
CONTENT_REVIEW_ROLES = ('super_admin', 'ops', 'editor')
AUDIENCE_MANAGEMENT_ROLES = ('super_admin', 'ops', 'revenue')
ROLE_LABELS = {
    'super_admin': 'Super Admin',
    'ops': 'Operations',
    'editor': 'Editor',
    'revenue': 'Revenue',
    'read_only': 'Read Only',
}
