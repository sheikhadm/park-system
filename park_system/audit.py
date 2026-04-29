from .models import AuditLog

def log_action(action, performed_by = None, ticket=None, vehicle=None, metadata=None):
    AuditLog.objects.create(
        action=action,
        performed_by=performed_by,
        ticket=ticket,
        vehicle=vehicle,
        metadata=metadata or {}
    )