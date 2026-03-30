from django.db import models
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.utils import timezone
import uuid

plate_validator = RegexValidator(
    regex=r'^[A-Za-z]{3}-\d{3}-[A-Za-z]{2}$',
    message="Number plate must be in format: ABC-123DE"
)
# Create your models here.
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

    def save(self, *args, **kwargs):
        self.number_plate = self.number_plate.upper()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.vehicle_make


class Ticket(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE)
    code = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    entry_time = models.DateTimeField(auto_now_add=True)
    exit_time = models.DateTimeField(null=True, blank=True)
    def close_session(self):
        if self.exit_time is not None:
            raise ValueError("Session already closed")

        self.exit_time = timezone.now()
        self.save()
