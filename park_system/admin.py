from django.contrib import admin
from .models import Vehicle, ParkingSlot, Ticket, UserProfile, AuditLog
from .models import Ticket

# Register your models here.


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role']
    list_editable = ['role']  # ← change roles directly in the list

admin.site.register(Vehicle)
admin.site.register(Ticket)
admin.site.register(ParkingSlot)

admin.site.register(AuditLog)