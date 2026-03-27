from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid

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
    number_plate = models.CharField(max_length = 100)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    
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
