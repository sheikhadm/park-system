from django_q.models import Schedule


def create_schedules():
    """
    Creates the schedules if they don't already exist.
    Call this once on startup.
    """

    # check overdue tickets every 15 minutes
    if not Schedule.objects.filter(name='check_overdue_tickets').exists():
        Schedule.objects.create(
            name='check_overdue_tickets',
            func='park_system.tasks.check_overdue_tickets',
            schedule_type=Schedule.MINUTES,
            minutes=15,
            repeats=-1  # -1 means repeat forever
        )
        print("✓ Overdue ticket schedule created")

    # check flagged vehicles every 5 minutes
    if not Schedule.objects.filter(name='check_flagged_vehicles').exists():
        Schedule.objects.create(
            name='check_flagged_vehicles',
            func='park_system.tasks.check_flagged_vehicles',
            schedule_type=Schedule.MINUTES,
            minutes=5,
            repeats=-1
        )
        print("✓ Flagged vehicle schedule created")