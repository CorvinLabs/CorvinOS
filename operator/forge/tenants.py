"""Forge tenants module - stub for compatibility."""
import os

def get_tenant_id():
    return os.environ.get('CORVIN_TENANT_ID', '_default')

def get_tenant_home(tenant_id=None):
    if not tenant_id:
        tenant_id = get_tenant_id()
    return os.path.expanduser(f'~/.corvin/tenants/{tenant_id}')

__all__ = ['get_tenant_id', 'get_tenant_home']
