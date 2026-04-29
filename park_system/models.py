from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
import uuid
import math

plate_validator = RegexValidator(
    regex=r'^[A-Za-z]{3}-\d{3}-[A-Za-z]{2}$',
    message="Number plate must be in format: ABC-123-DE"
)
# Create your models here.
class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        ATTENDANT = 'attendant', 'Attendant'
        CUSTOMER = 'customer', 'Customer'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CUSTOMER
    )

    def __str__(self):
        return f"{self.user.username} - {self.role}"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_attendant(self):
        return self.role == self.Role.ATTENDANT

    @property
    def is_customer(self):
        return self.role == self.Role.CUSTOMER


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_profile(sender, instance, **kwargs):
    instance.profile.save()



class Vehicle(models.Model):

    class VehicleType(models.TextChoices):
        CAR = 'car', 'Car'
        BIKE = 'bike', 'Bike'
        TRUCK = 'truck', 'Truck'
        BUS = 'bus', 'Bus'

    vehicle_type = models.CharField(
        max_length=10,
        choices=VehicleType.choices
    )
    vehicle_make = models.CharField(max_length = 100)
    number_plate = models.CharField(max_length = 100,validators=[plate_validator],unique=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=False)  
    flagged = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        self.number_plate = self.number_plate.upper()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.vehicle_make

class ParkingSlot(models.Model):
    slot_number = models.PositiveIntegerField(unique=True)
    is_occupied = models.BooleanField(default=False)

    
    def occupy_slot(self):
        """Call only when vehicle physically enters the bay."""
        updated = ParkingSlot.objects.filter(
            id=self.id,
            is_occupied=False  # guard — refuse if already occupied
        ).update(is_occupied=True)

        if updated == 0:
            raise ValueError(f"Slot {self.slot_number} is already occupied")
        self.refresh_from_db()

    def free_slot(self):
        """Call only when vehicle physically exits the bay."""
        updated = ParkingSlot.objects.filter(
            id=self.id,
            is_occupied=True  # guard — refuse if already free
        ).update(is_occupied=False)

        if updated == 0:
            raise ValueError(f"Slot {self.slot_number} is already free")
        self.refresh_from_db()

    def __str__(self):
        return f"Slot {self.slot_number}"

        
class Ticket(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE)
    code = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    slot = models.ForeignKey(
        ParkingSlot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    entry_time = models.DateTimeField(auto_now_add=True)
    exit_time = models.DateTimeField(null=True, blank=True)
    vehicle_exited = models.BooleanField(default=False)
    amount = models.PositiveIntegerField(null=True, blank=True)
    overdue = models.BooleanField(default=False)         
    overdue_since = models.DateTimeField(null=True, blank=True)
    payment_status = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)

    def mark_paid(self):
        if self.payment_status:
            raise ValueError("Ticket already paid")
        if self.amount is None or self.exit_time is None:
            raise ValueError("Fee not calculated yet — close session first")
        updated = Ticket.objects.filter(id=self.id,payment_status=False,exit_time__isnull=False).update(payment_status=True,paid_at=timezone.now())

        if updated == 0:
            raise ValueError("Ticket cannot be paid (already paid or not closed)")
        self.refresh_from_db()

    def close_session(self):
        if self.exit_time is not None :
            raise ValueError("Session already closed")
        
        self.exit_time = timezone.now()
        duration = self.exit_time - self.entry_time
        hours = duration.total_seconds() / 3600
        self.amount = math.ceil(hours) * 1000
        updated = Ticket.objects.filter(id=self.id,exit_time__isnull=True ).update(exit_time=self.exit_time,amount=self.amount)

        if updated == 0:
       
            raise ValueError("Session was already closed by another request")

        self.refresh_from_db()
    
    def reopen_session(self):
        if self.exit_time is None:
            raise ValueError("Session is still active — nothing to reopen")
        if self.payment_status:
            raise ValueError("Cannot reopen a paid ticket")

        updated = Ticket.objects.filter(
            id=self.id,
            payment_status=False,       # guard: only unpaid
            exit_time__isnull=False ,
            vehicle_exited=False    
        ).update(
            exit_time=None,
            amount=None
        )

        if updated == 0:
            raise ValueError("Ticket could not be reopened — it may already be active or paid")

        self.refresh_from_db()
    
    def save(self, *args, **kwargs):
        if self.pk:  # only check on updates, not on creation
            old = Ticket.objects.filter(pk=self.pk).values('amount', 'payment_status').first()
            if old and old['payment_status'] and old['amount'] != self.amount:
                raise ValueError("Amount cannot be changed after payment")
            if old['exit_time'] is not None and old['amount'] != self.amount:
                raise ValueError("Amount cannot be changed after session is closed")

        super().save(*args, **kwargs)
        
    
    class Meta:
        constraints = [
        # Only one active session per vehicle at a time
        models.UniqueConstraint(
            fields=["vehicle"],
            condition=Q(exit_time__isnull=True),
            name="unique_active_ticket_per_vehicle"
        ),

        # Only one active session per slot at a time
        # Slot stays held until vehicle_exited=True
        models.UniqueConstraint(
            fields=["slot"],
            condition=Q(vehicle_exited=False),
            name="unique_occupied_slot"
        ),

        # ACTIVE state: exit_time null means amount must also be null
        models.CheckConstraint(
            condition=Q(exit_time__isnull=False) | Q(amount__isnull=True),
            name="active_ticket_has_no_amount"
        ),

        # CLOSED state: exit_time set means amount must be set
        models.CheckConstraint(
            condition=Q(exit_time__isnull=True) | Q(amount__isnull=False),
            name="closed_ticket_requires_amount"
        ),

        # Payment only on closed ticket — can't pay before session ends
        models.CheckConstraint(
            condition=Q(payment_status=False) | Q(exit_time__isnull=False, amount__isnull=False),
            name="valid_payment_state"
        ),

        # Paid ticket must have amount — redundant but explicit
        models.CheckConstraint(
            condition=~Q(payment_status=True, amount__isnull=True),
            name="paid_ticket_must_have_amount"
        ),

        # EXITED state: vehicle_exited=True requires payment complete
        # Can't mark vehicle as exited without payment
        models.CheckConstraint(
            condition=Q(vehicle_exited=False) | Q(payment_status=True, exit_time__isnull=False),
            name="exited_requires_payment_and_close"
        ),

        # EXITED state: vehicle_exited=True requires exit_time set
        models.CheckConstraint(
            condition=Q(vehicle_exited=False) | Q(exit_time__isnull=False),
            name="exited_requires_closed_session"
        ),
    ]

class AuditLog(models.Model):
    class Action(models.TextChoices):
        SESSION_STARTED = 'session_started', 'Session Started'
        SESSION_CLOSED = 'session_closed', 'Session Closed'
        PAYMENT_RECORDED = 'payment_recorded', 'Payment Recorded'
        PAYMENT_INITIATED = 'payment_initiated', 'Payment Initiated'
        PAYMENT_FAILED = 'payment_failed', 'Payment Failed'
        FEE_OVERRIDDEN = 'fee_overridden', 'Fee Overridden'
        VEHICLE_FLAGGED = 'vehicle_flagged', 'Vehicle Flagged'
        VEHICLE_UNFLAGGED = 'vehicle_unflagged', 'Vehicle Unflagged'
        VEHICLE_ACTIVATED = 'vehicle_activated', 'Vehicle activated'
        VEHICLE_DISACTIVATED = 'vehicle_disactivated', 'Vehicle disactivated'
        VEHICLE_ADDED = 'vehicle_added', 'Vehicle Added'
        VEHICLE_DELETED = 'vehicle_deleted', 'Vehicle Deleted'
        VEHICLE_FLAGGED_AUTO = 'vehicle_flagged_auto', 'Vehicle Flagged (Automatic)'
        TICKET_REOPENED = 'ticket_reopened', 'Ticket Reopened'
        ROLE_CHANGED = 'role_changed', 'Role Changed'
        CUSTOMER_REGISTERED = 'customer_registered', 'Customer Registered'


    action = models.CharField(max_length=50, choices=Action.choices)
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs'
    )
    ticket = models.ForeignKey(
        'Ticket',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    vehicle = models.ForeignKey(
        'Vehicle',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict)  # stores extra context per action

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['action']),
            models.Index(fields=['performed_by']),
            models.Index(fields=['ticket']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"{self.performed_by} — {self.action} at {self.timestamp}"
    


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'
        ABANDONED = 'abandoned', 'Abandoned'

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    initiated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='initiated_payments'
    )
    reference = models.CharField(max_length=100, unique=True)
    amount = models.PositiveIntegerField()  # in kobo (Paystack uses kobo)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    paystack_id = models.CharField(max_length=100, blank=True, null=True)
    channel = models.CharField(max_length=50, blank=True, null=True)  # card, bank, ussd
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    meta = models.JSONField(default=dict)  # raw paystack response for audit

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment {self.reference} — {self.status}"

    @property
    def amount_naira(self):
        return self.amount // 100  # convert kobo back to naira for display
