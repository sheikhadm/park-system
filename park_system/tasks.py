from django.utils import timezone
from datetime import timedelta
from .models import Ticket, Vehicle, AuditLog
from .audit import log_action

OVERDUE_LIMIT_HOURS = 9


def check_overdue_tickets():
    """
    Marks tickets as overdue if car has been parked
    longer than OVERDUE_LIMIT_HOURS
    """
    now = timezone.now()
    overdue_threshold = now - timedelta(hours=OVERDUE_LIMIT_HOURS)

    overdue_tickets = Ticket.objects.filter(
        exit_time__isnull=True,
        overdue=False,
        entry_time__lte=overdue_threshold
    )

    count = 0
    for ticket in overdue_tickets:
        ticket.overdue = True
        ticket.overdue_since = now
        ticket.save()
        count += 1

    print(f"{count} tickets marked as overdue")
    return count


def check_flagged_vehicles():
    """
    Flags vehicles whose is_active is still True
    20 minutes after their last session ended.
    Used as a periodic sweep for anything missed by check_single_vehicle_flagged.
    """
    now = timezone.now()
    flag_threshold = now - timedelta(minutes=20)

    closed_tickets = Ticket.objects.filter(
        exit_time__isnull=False,
        exit_time__lte=flag_threshold,
        vehicle__is_active=True,
        vehicle__flagged=False
    ).select_related('vehicle')

    count = 0
    for ticket in closed_tickets:
        vehicle = ticket.vehicle
        vehicle.flagged = True
        vehicle.save()

        log_action(
            action=AuditLog.Action.VEHICLE_FLAGGED_AUTO,
            performed_by=None,
            vehicle=vehicle,
            metadata={
                "reason": "Periodic sweep — vehicle still active 20min after session closed",
                "number_plate": vehicle.number_plate,
                "session_exit_time": str(ticket.exit_time),
            }
        )
        count += 1

    print(f"{count} vehicles flagged")
    return count


def check_single_vehicle_flagged(vehicle_id):
    """
    Checks one specific vehicle 20 minutes after its session ended.
    Called from end_session view via schedule().
    Flags if:
    - vehicle is still marked active (never scanned out), OR
    - vehicle has a closed unpaid ticket (exited without paying)
    """
    try:
        vehicle = Vehicle.objects.get(id=vehicle_id)
    except Vehicle.DoesNotExist:
        return

    # Check 1: still active after session closed — attendant never scanned exit
    if vehicle.is_active and not vehicle.flagged:
        vehicle.flagged = True
        vehicle.save()

        log_action(
            action=AuditLog.Action.VEHICLE_FLAGGED_AUTO,
            performed_by=None,
            vehicle=vehicle,
            metadata={
                "reason": "Vehicle still active 20 minutes after session ended — exit not scanned",
                "number_plate": vehicle.number_plate,
            }
        )
        print(f"Vehicle {vehicle.number_plate} flagged — exit not scanned")
        return

    # Check 2: closed session exists with no payment — revenue leak
    unpaid_closed = Ticket.objects.filter(
        vehicle=vehicle,
        exit_time__isnull=False,
        payment_status=False
    ).exists()

    if unpaid_closed and not vehicle.flagged:
        vehicle.flagged = True
        vehicle.save()

        log_action(
            action=AuditLog.Action.VEHICLE_FLAGGED_AUTO,
            performed_by=None,
            vehicle=vehicle,
            metadata={
                "reason": "Closed session with no payment recorded — possible revenue leak",
                "number_plate": vehicle.number_plate,
            }
        )
        print(f"Vehicle {vehicle.number_plate} flagged — unpaid closed session")