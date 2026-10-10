"""Pick one tenant store at process start; SQLite fallback is retained for rollback.

Never switch backends on a live process. PostgreSQL migration is a separate operator step.
"""
import os
if os.getenv('SMARTBIZ_TENANT_BACKEND', 'sqlite').strip().lower() == 'postgres':
    from . import tenant_store_pg as store
else:
    from . import tenant_store as store
